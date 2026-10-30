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
from isik.django.apps.notifications.choices import Policy, PushEnvironment
from isik.django.apps.notifications.swapping import get_kind_policy_model


class SubscriptionRefused(ValueError):
    """Why a subscription can't be changed as asked. `code` says which refusal it is, for a client."""

    code = "subscription_refused"


class KindIsMandatory(SubscriptionRefused):
    """A MANDATORY kind reaches everyone its audience names: there's no choice about it to make, or to force."""

    code = "kind_mandatory"


class SubscriptionForced(SubscriptionRefused):
    """The subscription is forced by staff - the person can't change it until it's released."""

    code = "subscription_forced"


def _kind_name(kind):
    # A Kind's name, or a policy row's, or the name itself.
    return str(getattr(kind, "name", kind))


def _choosable_kind(kind):
    """
    The policy row of `kind` - a row, as given, or read by its name - refused when there's none, or it's
    MANDATORY.
    """
    model = get_kind_policy_model()
    row = kind if isinstance(kind, model) else model._default_manager.filter(name=_kind_name(kind)).first()
    if row is None:
        raise ValueError(_("No kind is called %(kind)s.") % {"kind": _kind_name(kind)})
    if row.policy == Policy.MANDATORY:
        raise KindIsMandatory(_("%(kind)s is mandatory - it reaches everyone it's for.") % {"kind": row.name})
    return row


def _forceable():
    return issubclass(get_subscription_model(), AbstractForceableSubscription)


def _refuse_forced(rows):
    if _forceable() and rows.filter(forced=True).exists():
        raise SubscriptionForced(_("This subscription is forced by staff, so it can't be changed."))


def subscribe(user, kind, variant=None, *, enabled=True):
    """
    Turns `kind` on (or off, with `enabled=False`) for `user` - for every variant, or only `variant`. For
    every variant, the user's rows naming one go first, in the same transaction: a row naming no variant
    already covers them. Naming one beside a row covering every variant is refused - `unsubscribe()` the
    kind first. Returns the subscription.
    """
    model = get_subscription_model()
    policy = _choosable_kind(kind)
    name = policy.name
    with transaction.atomic():
        rows = model._default_manager.filter(user=user, kind=policy)
        _refuse_forced(rows if variant is None else rows.filter(variant=variant))
        if variant is None:
            rows.filter(variant__isnull=False).delete()
        elif rows.filter(variant__isnull=True).exists():
            raise ValueError(
                _("%(user)s has a subscription to every variant of %(kind)s - unsubscribe it before naming one.")
                % {"user": user, "kind": name}
            )
        try:
            with transaction.atomic():
                # A row not of their choosing is a forced one, refused above - so this one is chosen, as made.
                subscription, _created = model._default_manager.update_or_create(
                    user=user, kind=policy, variant=variant, defaults={"enabled": enabled}
                )
                # Its kind as read already - a row it updated comes back without it.
                subscription.kind = policy
        except IntegrityError as error:
            # Another subscribe() of the same user to the same kind, one naming a variant and one naming
            # none, got there first - the database's guard refused this one.
            raise ValueError(
                _("%(user)s's subscriptions to %(kind)s were changed at the same time - try again.")
                % {"user": user, "kind": name}
            ) from error
    return subscription


def unsubscribe(user, kind, variant=None):
    """
    Forgets `user`'s choice about `kind` - every variant's, or only `variant`'s - so the kind's policy
    decides again. Returns how many subscriptions went.
    """
    rows = get_subscription_model()._default_manager.filter(user=user, kind__name=_kind_name(kind))
    if variant is not None:
        rows = rows.filter(variant=variant)
    _refuse_forced(rows)
    deleted, _per_model = rows.delete()
    return deleted


def force_subscription(user, kind, variant=None, *, enabled, source, reason):
    """
    Forces `kind` on (or off) for `user` - every variant, or only `variant` - so they can't change it until
    it's released. Their own choice is kept and comes back on release. Forcing the whole kind drops their own
    per-variant choices, as `subscribe()` does. Refused for a MANDATORY kind, a source that isn't declared or a
    reason short of its floor (`ForceRefused`), and on a subscription model that isn't forceable. Tells the
    person, when `SUBSCRIPTION_FORCED_KIND` names a kind. Returns the subscription.
    """
    model = get_subscription_model()
    if not _forceable():
        raise ImproperlyConfigured(
            _("%(model)s can't be forced - base it on AbstractForceableSubscription.") % {"model": model._meta.label}
        )
    policy = _choosable_kind(kind)
    name = policy.name
    declared = checked_force(source, reason)
    with transaction.atomic():
        rows = model._default_manager.filter(user=user, kind=policy)
        if variant is None:
            _refuse_forced(rows.filter(variant__isnull=False))
            rows.filter(variant__isnull=False).delete()
        elif rows.filter(variant__isnull=True).exists():
            raise SubscriptionRefused(
                _("%(user)s has a choice about every variant of %(kind)s - force the kind, not one variant.")
                % {"user": user, "kind": name}
            )
        force = {"forced": True, "forced_enabled": enabled, "forced_source": declared.name, "forced_reason": reason}
        subscription = rows.filter(variant=variant).first()
        if subscription is None:
            subscription = model._default_manager.create(user=user, kind=policy, variant=variant, chosen=False, **force)
        else:
            for field, value in force.items():
                setattr(subscription, field, value)
            subscription.save()
        _tell(user, name, variant, forced=True, enabled=enabled)
    return subscription


def release_subscription(user, kind, variant=None, *, by_operator=True):
    """
    Lifts the force on `user`'s subscription to `kind` (or `variant`), so their own choice decides again - or
    the kind's policy, when they'd made none. Refused where the force's source doesn't let staff lift it and
    `by_operator` is True. Tells the person, when `SUBSCRIPTION_FORCED_KIND` names a kind. Returns whether one
    was released.
    """
    name = _kind_name(kind)
    with transaction.atomic():
        subscription = (
            get_subscription_model()
            ._default_manager.select_for_update()
            .filter(user=user, kind__name=name, variant=variant, forced=True)
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
        _tell(user, name, variant, forced=False, enabled=None)
    return True


def _tell(user, kind, variant, *, forced, enabled):
    told = conf.setting("SUBSCRIPTION_FORCED_KIND")
    if told is None:
        return
    from isik.django.apps.notifications.kinds import raise_notice

    raise_notice(
        told,
        snapshot={"user": user.pk, "kind": kind, "variant": variant, "forced": forced, "enabled": enabled},
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
