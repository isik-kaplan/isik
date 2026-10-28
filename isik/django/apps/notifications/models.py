"""
The ledger isik ships: what happened (`Notice`), who was to be told and how (`Delivery`, one child per
channel), each try at telling them (`DeliveryAttempt`), the per-partition progress of a notice reaching
many partitions (`FanOutShard`), and each declared kind's mirrored policy (`NoticeKindPolicy`). Each is
swapped out by its `ISIK_NOTIFICATIONS_*_MODEL` setting, for a project owning it - see `bases.py`.
"""

from isik._internal.translation import gettext_lazy
from isik.django.apps.notifications.bases import (
    AbstractDelivery,
    AbstractDeliveryAttempt,
    AbstractEmailDelivery,
    AbstractFanOutShard,
    AbstractInboxDelivery,
    AbstractNotice,
    AbstractNoticeKindPolicy,
    AbstractWebhookDelivery,
)
from isik.django.apps.notifications.choices import DeliveryState, Policy, Scope, ShardState, Source


class NoticeKindPolicy(AbstractNoticeKindPolicy):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_KIND_POLICY_MODEL"
        verbose_name = gettext_lazy("notice kind")


class Notice(AbstractNotice):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_NOTICE_MODEL"
        verbose_name = gettext_lazy("notice")


class FanOutShard(AbstractFanOutShard):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_FAN_OUT_SHARD_MODEL"
        verbose_name = gettext_lazy("fan-out shard")


class Delivery(AbstractDelivery):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_DELIVERY_MODEL"
        verbose_name = gettext_lazy("delivery")
        verbose_name_plural = gettext_lazy("deliveries")


class WebhookDelivery(AbstractWebhookDelivery):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_WEBHOOK_DELIVERY_MODEL"
        verbose_name = gettext_lazy("webhook delivery")
        verbose_name_plural = gettext_lazy("webhook deliveries")


class EmailDelivery(AbstractEmailDelivery):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_EMAIL_DELIVERY_MODEL"
        verbose_name = gettext_lazy("email delivery")
        verbose_name_plural = gettext_lazy("email deliveries")


class InboxDelivery(AbstractInboxDelivery):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_INBOX_DELIVERY_MODEL"
        verbose_name = gettext_lazy("inbox delivery")
        verbose_name_plural = gettext_lazy("inbox deliveries")


class DeliveryAttempt(AbstractDeliveryAttempt):
    class Meta:
        swappable = "ISIK_NOTIFICATIONS_DELIVERY_ATTEMPT_MODEL"
        verbose_name = gettext_lazy("delivery attempt")


__all__ = [
    "Delivery",
    "DeliveryAttempt",
    "DeliveryState",
    "EmailDelivery",
    "FanOutShard",
    "InboxDelivery",
    "Notice",
    "NoticeKindPolicy",
    "Policy",
    "Scope",
    "ShardState",
    "Source",
    "WebhookDelivery",
]
