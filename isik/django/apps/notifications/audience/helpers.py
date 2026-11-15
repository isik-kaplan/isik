"""
Changing a user's subscriptions - their own choices, and, on a forceable subscription model, what staff force -
reading and marking their inbox, and registering and forgetting the devices pushes go to.
"""

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.common.forcing import checked_force, checked_release
from isik.django.apps.notifications import conf
from isik.django.apps.notifications.audience.bases import AbstractForceableSubscription
from isik.django.apps.notifications.audience.swapping import (
    get_device_model,
    get_inbox_item_model,
    get_subscription_model,
)
from isik.django.apps.notifications.choices import CHOSEN, PushEnvironment
from isik.django.apps.notifications.swapping import get_kind_policy_model


class SubscriptionRefused(ValueError):
    """Why a subscription can't be changed as asked. `code` says which refusal it is, for a client."""

    code = "subscription_refused"


class KindIsMandatory(SubscriptionRefused):
    """
    A kind leaving no channel to a person's choice - mandatory, or never sent, on each - reaches everyone its
    audience names wherever it's sent: there's no choice about it to make, or to force.
    """

    code = "kind_mandatory"


class ChannelNotChosen(SubscriptionRefused):
    """The kind leaves no choice on the channel named - it's mandatory there, or never sent there."""

    code = "channel_not_chosen"


class SubscriptionForced(SubscriptionRefused):
    """The subscription is forced by staff - the person can't change it until it's released."""

    code = "subscription_forced"


def _kind_name(kind):
    # A Kind's name, or a policy row's, or the name itself.
    return str(getattr(kind, "name", kind))


def _choosable_kind(kind, channel):
    """
    The policy row of `kind` - a row, as given, or read by its name - refused when there's none, when it leaves no
    choice on `channel`, or, with no channel, on any.
    """
    model = get_kind_policy_model()
    row = kind if isinstance(kind, model) else model._default_manager.filter(name=_kind_name(kind)).first()
    if row is None:
        raise ValueError(_("No kind is called %(kind)s.") % {"kind": _kind_name(kind)})
    if channel is not None and row.channels.get(channel) not in CHOSEN:
        raise ChannelNotChosen(
            _("%(kind)s leaves no choice on %(channel)s - it's mandatory there, or never sent there.")
            % {"kind": row.name, "channel": channel}
        )
    if channel is None and not any(policy in CHOSEN for policy in row.channels.values()):
        raise KindIsMandatory(
            _("%(kind)s leaves no choice on any channel - it reaches everyone it's for, wherever it's sent.")
            % {"kind": row.name}
        )
    return row


def _forceable():
    return issubclass(get_subscription_model(), AbstractForceableSubscription)


def _refuse_forced(rows):
    if _forceable() and any(row.forced for row in rows):
        raise SubscriptionForced(_("This subscription is forced by staff, so it can't be changed."))


def _key(row):
    return row.variant, row.channel


def _covers(wide, narrow):
    """Whether the (variant, channel) `wide` covers `narrow` - each part the same, or none: every one."""
    (variant, channel), (other_variant, other_channel) = wide, narrow
    return variant in (None, other_variant) and channel in (None, other_channel)


def _overlaps(one, other):
    """Whether the (variant, channel)s `one` and `other` cover any of the same - each part the same, or one none."""
    return all(None in pair or pair[0] == pair[1] for pair in ((one[0], other[0]), (one[1], other[1])))


def _around(rows, key):
    """
    `rows` (a person's, for one kind) around the (variant, channel) `key`: (the one naming it, if any; those it
    covers; those it clashes with - covering it, or covering part of it).
    """
    same = next((row for row in rows if _key(row) == key), None)
    covered = [row for row in rows if row is not same and _covers(key, _key(row))]
    clashing = [row for row in rows if row is not same and row not in covered and _overlaps(_key(row), key)]
    return same, covered, clashing


def subscribe(user, kind, variant=None, *, channel=None, enabled=True):
    """
    Turns `kind` on (or off, with `enabled=False`) for `user` - for every variant, or only `variant`, and on every
    channel the kind leaves to them, or only `channel`. The user's rows it covers - naming a variant or a channel
    where it names none - go first, in the same transaction. Naming one beside a row covering it, or part of it,
    is refused - `unsubscribe()` that one first. Returns the subscription.
    """
    model = get_subscription_model()
    policy = _choosable_kind(kind, channel)
    name = policy.name
    with transaction.atomic():
        rows = model._default_manager.filter(user=user, kind=policy)
        same, covered, clashing = _around(list(rows), (variant, channel))
        if clashing:
            raise ValueError(
                _("%(user)s has a subscription to %(kind)s covering this one - unsubscribe it before naming this one.")
                % {"user": user, "kind": name}
            )
        _refuse_forced([*covered, *([same] if same is not None else [])])
        rows.filter(pk__in=[row.pk for row in covered]).delete()
        try:
            with transaction.atomic():
                # A row not of their choosing is a forced one, refused above - so this one is chosen, as made.
                subscription, _created = model._default_manager.update_or_create(
                    user=user, kind=policy, variant=variant, channel=channel, defaults={"enabled": enabled}
                )
                # Its kind as read already - a row it updated comes back without it.
                subscription.kind = policy
        except IntegrityError as error:
            # Another subscribe() of the same user to the same kind, one covering the other, got there first -
            # the database's guard refused this one.
            raise ValueError(
                _("%(user)s's subscriptions to %(kind)s were changed at the same time - try again.")
                % {"user": user, "kind": name}
            ) from error
    return subscription


def unsubscribe(user, kind, variant=None, *, channel=None):
    """
    Forgets `user`'s choice about `kind` - every variant's, or only `variant`'s; on every channel, or only
    `channel` - so the kind's policy decides again. Returns how many subscriptions went.
    """
    rows = get_subscription_model()._default_manager.filter(user=user, kind__name=_kind_name(kind))
    if variant is not None:
        rows = rows.filter(variant=variant)
    if channel is not None:
        rows = rows.filter(channel=channel)
    _refuse_forced(rows)
    deleted, _per_model = rows.delete()
    return deleted


def force_subscription(user, kind, variant=None, *, channel=None, enabled, source, reason):
    """
    Forces `kind` on (or off) for `user` - every variant, or only `variant`; every channel the kind leaves to
    them, or only `channel` - so they can't change it until it's released. Their own choice is kept and comes back
    on release. Forcing more than one choice covers drops those it covers, as `subscribe()` does. Refused for a
    kind leaving no choice there, a source that isn't declared or a reason short of its floor (`ForceRefused`),
    and on a subscription model that isn't forceable. Tells the person, when `SUBSCRIPTION_FORCED_KIND` names a
    kind. Returns the subscription.
    """
    model = get_subscription_model()
    if not _forceable():
        raise ImproperlyConfigured(
            _("%(model)s can't be forced - base it on AbstractForceableSubscription.") % {"model": model._meta.label}
        )
    policy = _choosable_kind(kind, channel)
    name = policy.name
    declared = checked_force(source, reason)
    with transaction.atomic():
        rows = model._default_manager.filter(user=user, kind=policy)
        subscription, covered, clashing = _around(list(rows), (variant, channel))
        if clashing:
            raise SubscriptionRefused(
                _("%(user)s has a choice about %(kind)s covering this one - force that one.")
                % {"user": user, "kind": name}
            )
        _refuse_forced(covered)
        rows.filter(pk__in=[row.pk for row in covered]).delete()
        force = {"forced": True, "forced_enabled": enabled, "forced_source": declared.name, "forced_reason": reason}
        if subscription is None:
            subscription = model._default_manager.create(
                user=user, kind=policy, variant=variant, channel=channel, chosen=False, **force
            )
        else:
            for field, value in force.items():
                setattr(subscription, field, value)
            subscription.save()
        _tell(user, name, variant, channel, forced=True, enabled=enabled)
    return subscription


def release_subscription(user, kind, variant=None, *, channel=None, by_operator=True):
    """
    Lifts the force on `user`'s subscription to `kind` (or `variant`, or `channel`), so their own choice decides
    again - or the kind's policy, when they'd made none. Refused where the force's source doesn't let staff lift it
    and `by_operator` is True. Tells the person, when `SUBSCRIPTION_FORCED_KIND` names a kind. Returns whether one
    was released.
    """
    name = _kind_name(kind)
    with transaction.atomic():
        subscription = (
            get_subscription_model()
            ._default_manager.select_for_update()
            .filter(user=user, kind__name=name, variant=variant, channel=channel, forced=True)
            .first()
        )
        if subscription is None:
            return False
        checked_release(subscription.forced_source, by_operator=by_operator)
        if subscription.chosen:
            subscription.forced, subscription.forced_enabled = False, None
            subscription.forced_source, subscription.forced_reason = "", ""
            subscription.save()
        else:
            subscription.delete()
        _tell(user, name, variant, channel, forced=False, enabled=None)
    return True


def _tell(user, kind, variant, channel, *, forced, enabled):
    told = conf.setting("SUBSCRIPTION_FORCED_KIND")
    if told is None:
        return
    from isik.django.apps.notifications.kinds import raise_notice

    raise_notice(
        told,
        snapshot={
            "user": user.pk,
            "kind": kind,
            "variant": variant,
            "channel": channel,
            "forced": forced,
            "enabled": enabled,
        },
    )


def unread(user):
    """`user`'s inbox items not yet read, newest first."""
    return get_inbox_item_model()._default_manager.filter(user=user, read_at__isnull=True)


def mark_read(items):
    """Marks every unread item of the `items` queryset read now. Returns how many it marked."""
    return items.filter(read_at__isnull=True).update(read_at=timezone.now())


def register_device(user, platform, token, *, environment=PushEnvironment.PRODUCTION, app_id=""):
    """
    Registers `token` - an app install's, on `platform` - as `user`'s device: made, or, already known, moved
    to `user` and active again, since a token is one install and whoever registers it is signed in on it now.
    Returns the device.
    """
    device, _made = get_device_model()._default_manager.update_or_create(
        platform=platform,
        token=token,
        defaults={
            "user": user,
            "environment": environment,
            "app_id": app_id,
            "is_active": True,
            "last_seen_at": timezone.now(),
        },
    )
    return device


def forget_devices(user):
    """Forgets every device of `user`, so no push reaches them - at signing out everywhere, say. Returns how many."""
    return get_device_model()._default_manager.filter(user=user).delete()[0]
