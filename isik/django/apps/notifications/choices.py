"""The choices the ledger's columns hold - and a kind's declaration names."""

from django.db import models

from isik._internal.translation import gettext_lazy


class Policy(models.TextChoices):
    """Who gets a kind: everyone its audience names, everyone but those opting out, or only those opting in."""

    MANDATORY = "mandatory", gettext_lazy("Mandatory")
    DEFAULT_ON = "default_on", gettext_lazy("On unless turned off")
    DEFAULT_OFF = "default_off", gettext_lazy("Off unless turned on")


class Scope(models.TextChoices):
    """Whether a kind's audience lives in one partition or is spread over many."""

    SINGLE_PARTITION = "single_partition", gettext_lazy("One partition")
    CROSS_PARTITION = "cross_partition", gettext_lazy("Many partitions")


class Source(models.TextChoices):
    """
    What writes a kind's notices: a trigger on its model, the application calling raise_notice(), or
    record_email() recording an email its caller sent.
    """

    TRIGGER = "trigger", gettext_lazy("A trigger")
    APPLICATION = "application", gettext_lazy("The application")
    RECORDED = "recorded", gettext_lazy("A record of an email sent")


class DeliveryState(models.TextChoices):
    PENDING = "pending", gettext_lazy("Pending")
    SENDING = "sending", gettext_lazy("Sending")
    SENT = "sent", gettext_lazy("Sent")
    ABANDONED = "abandoned", gettext_lazy("Abandoned")


class ShardState(models.TextChoices):
    PENDING = "pending", gettext_lazy("Pending")
    CLAIMED = "claimed", gettext_lazy("Claimed")
    FINISHED = "finished", gettext_lazy("Finished")
    ABANDONED = "abandoned", gettext_lazy("Abandoned")


class Platform(models.TextChoices):
    """The service a device's pushes go through - Apple's for an iOS token, Firebase's for an FCM one."""

    APNS = "apns", gettext_lazy("Apple Push Notification service")
    FCM = "fcm", gettext_lazy("Firebase Cloud Messaging")


class PushEnvironment(models.TextChoices):
    """Which APNs a device's token was issued by: a development build's sandbox, or production."""

    PRODUCTION = "production", gettext_lazy("Production")
    SANDBOX = "sandbox", gettext_lazy("Sandbox")
