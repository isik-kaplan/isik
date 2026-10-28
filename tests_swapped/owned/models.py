"""Every notifications model, the project's own - empty subclasses of isik's bases, under tables of their own."""

import uuid

import pgtrigger
from django.conf import settings
from django.db import models

from isik.django.apps.notifications import bases
from isik.django.apps.notifications.audience import bases as audience
from isik.django.apps.notifications.audiences import Field
from isik.django.apps.notifications.kinds import declare_kind, notifies
from isik.django.apps.notifications.models import Policy


def table(name):
    return type("Meta", (), {"db_table": f"owned_{name}"})


class KindPolicy(bases.AbstractNoticeKindPolicy):
    Meta = table("kind_policy")


class Notice(bases.AbstractNotice):
    # Keyed by a UUID, as a project keying every table so would: its db_default is what a trigger-written
    # notice gets, and the Python default what one written from Python does.
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        db_default=models.Func(function="gen_random_uuid"),
        help_text="The notice's own identifier.",
    )

    Meta = table("notice")


class FanOutShard(bases.AbstractFanOutShard):
    Meta = table("fan_out_shard")


class Delivery(bases.AbstractDelivery):
    Meta = table("delivery")


class WebhookDelivery(bases.AbstractWebhookDelivery):
    Meta = table("webhook_delivery")


class EmailDelivery(bases.AbstractEmailDelivery):
    Meta = table("email_delivery")


class InboxDelivery(bases.AbstractInboxDelivery):
    Meta = table("inbox_delivery")


class DeliveryAttempt(bases.AbstractDeliveryAttempt):
    Meta = table("delivery_attempt")


def _user():
    return models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")


def _kind():
    return models.ForeignKey(KindPolicy, on_delete=models.CASCADE, related_name="+")


class Subscription(audience.AbstractSubscription):
    user = _user()
    kind = _kind()
    Meta = table("subscription")


class InboxItem(audience.AbstractInboxItem):
    user = _user()
    kind = _kind()
    Meta = table("inbox_item")


class Endpoint(audience.AbstractEndpoint):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        related_name="+",
        help_text="Whose endpoint it is.",
    )

    Meta = table("endpoint")


class EndpointKind(audience.AbstractEndpointKind):
    endpoint = models.ForeignKey(Endpoint, on_delete=models.CASCADE, related_name="kinds")
    kind = _kind()
    Meta = table("endpoint_kind")


PACKAGE_LOST = declare_kind("package_lost", variants=("stolen", "misplaced"))


@notifies(
    PACKAGE_LOST,
    on=pgtrigger.Update,
    variants={"stolen": pgtrigger.Q(new__state="stolen"), "misplaced": pgtrigger.Q(new__state="misplaced")},
    snapshot=["label", "owner"],
    audience=Field("owner"),
    policy=Policy.DEFAULT_ON,
)
class Package(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    label = models.CharField(max_length=50)
    state = models.CharField(max_length=20, default="moving")
