"""The audience models isik ships - each swapped out by its `ISIK_NOTIFICATIONS_*_MODEL` setting."""

from django.conf import settings
from django.db import models

from isik._internal.translation import gettext_lazy
from isik.django.apps.notifications.audience.bases import (
    AbstractEndpoint,
    AbstractEndpointKind,
    AbstractInboxItem,
    AbstractSubscription,
)
from isik.django.apps.notifications.audience.swapping import ENDPOINT_MODEL, endpoint_model_name
from isik.django.apps.notifications.swapping import KIND_POLICY_MODEL, model_name


def _kind():
    return models.ForeignKey(
        model_name(KIND_POLICY_MODEL),
        on_delete=models.CASCADE,
        related_name="+",
        help_text=gettext_lazy("The kind it's for."),
        db_comment="The kind it's for.",
    )


def _user():
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        help_text=gettext_lazy("The user it's for."),
        db_comment="The user it's for.",
    )


class Subscription(AbstractSubscription):
    user = _user()
    kind = _kind()

    class Meta:
        swappable = "ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL"
        verbose_name = gettext_lazy("subscription")


class InboxItem(AbstractInboxItem):
    user = _user()
    kind = _kind()

    class Meta(AbstractInboxItem.Meta):
        swappable = "ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL"
        verbose_name = gettext_lazy("inbox item")


class Endpoint(AbstractEndpoint):
    class Meta:
        swappable = ENDPOINT_MODEL
        verbose_name = gettext_lazy("endpoint")


class EndpointKind(AbstractEndpointKind):
    endpoint = models.ForeignKey(
        endpoint_model_name(),
        on_delete=models.CASCADE,
        related_name="kinds",
        help_text=gettext_lazy("The endpoint wanting the kind."),
        db_comment="The endpoint wanting the kind.",
    )
    kind = _kind()

    class Meta:
        swappable = "ISIK_NOTIFICATIONS_ENDPOINT_KIND_MODEL"
        verbose_name = gettext_lazy("endpoint kind")
