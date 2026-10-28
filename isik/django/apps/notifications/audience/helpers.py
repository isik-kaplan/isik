"""Changing a user's subscriptions, and reading and marking their inbox."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.notifications.audience.swapping import get_inbox_item_model, get_subscription_model


def _kind_name(kind):
    # A Kind's name, or a policy row's - whose str() is its kind - or the name itself.
    return str(getattr(kind, "name", kind))


def subscribe(user, kind, variant=None, *, enabled=True):
    """
    Turns `kind` on (or off, with `enabled=False`) for `user` - for every variant, or only `variant`. For
    every variant, the user's rows naming one go first, in the same transaction: a row naming no variant
    already covers them. Naming one beside a row covering every variant is refused - `unsubscribe()` the
    kind first. Returns the subscription.
    """
    model = get_subscription_model()
    name = _kind_name(kind)
    with transaction.atomic():
        rows = model._default_manager.filter(user=user, kind_id=name)
        if variant is None:
            rows.filter(variant__isnull=False).delete()
        elif rows.filter(variant__isnull=True).exists():
            raise ValueError(
                _("%(user)s has a subscription to every variant of %(kind)s - unsubscribe it before naming one.")
                % {"user": user, "kind": name}
            )
        try:
            with transaction.atomic():
                subscription, _created = model._default_manager.update_or_create(
                    user=user, kind_id=name, variant=variant, defaults={"enabled": enabled}
                )
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
    rows = get_subscription_model()._default_manager.filter(user=user, kind_id=_kind_name(kind))
    if variant is not None:
        rows = rows.filter(variant=variant)
    deleted, _per_model = rows.delete()
    return deleted


def unread(user):
    """`user`'s inbox items not yet read, newest first."""
    return get_inbox_item_model()._default_manager.filter(user=user, read_at__isnull=True)


def mark_read(items):
    """Marks every unread item of the `items` queryset read now. Returns how many it marked."""
    return items.filter(read_at__isnull=True).update(read_at=timezone.now())
