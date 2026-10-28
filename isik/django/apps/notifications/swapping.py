"""
The ledger models' swappable settings, and the getters reading them - `get_notice_model()` and its siblings.
Each setting names an `"app_label.Model"`; unset, it names the model isik ships. A project owning one
subclasses its abstract base (`bases.py`) and points the setting at it - its own table, migrations and
admin, with every trigger, constraint and index isik's has.

A migration naming a swappable model reads its setting by name (`settings.ISIK_NOTIFICATIONS_...`), as one
naming the user model reads `settings.AUTH_USER_MODEL` - so each is given its default here, when the
notifications apps load, for a project that sets none of them.
"""

from django.apps import apps
from django.conf import settings


KIND_POLICY_MODEL = "ISIK_NOTIFICATIONS_KIND_POLICY_MODEL"
NOTICE_MODEL = "ISIK_NOTIFICATIONS_NOTICE_MODEL"
FAN_OUT_SHARD_MODEL = "ISIK_NOTIFICATIONS_FAN_OUT_SHARD_MODEL"
DELIVERY_MODEL = "ISIK_NOTIFICATIONS_DELIVERY_MODEL"
WEBHOOK_DELIVERY_MODEL = "ISIK_NOTIFICATIONS_WEBHOOK_DELIVERY_MODEL"
EMAIL_DELIVERY_MODEL = "ISIK_NOTIFICATIONS_EMAIL_DELIVERY_MODEL"
INBOX_DELIVERY_MODEL = "ISIK_NOTIFICATIONS_INBOX_DELIVERY_MODEL"
DELIVERY_ATTEMPT_MODEL = "ISIK_NOTIFICATIONS_DELIVERY_ATTEMPT_MODEL"

DEFAULTS = {
    KIND_POLICY_MODEL: "isik_notifications.NoticeKindPolicy",
    NOTICE_MODEL: "isik_notifications.Notice",
    FAN_OUT_SHARD_MODEL: "isik_notifications.FanOutShard",
    DELIVERY_MODEL: "isik_notifications.Delivery",
    WEBHOOK_DELIVERY_MODEL: "isik_notifications.WebhookDelivery",
    EMAIL_DELIVERY_MODEL: "isik_notifications.EmailDelivery",
    INBOX_DELIVERY_MODEL: "isik_notifications.InboxDelivery",
    DELIVERY_ATTEMPT_MODEL: "isik_notifications.DeliveryAttempt",
}


def set_defaults():
    """Gives each swappable setting the project leaves unset its default."""
    for name, default in DEFAULTS.items():
        if not hasattr(settings, name):
            setattr(settings, name, default)


def model_name(setting):
    """The `"app_label.Model"` the swappable `setting` names - for a key to it, declared at class definition."""
    return getattr(settings, setting)


def _model(setting):
    # set_defaults() has given every setting a value by the time anything asks.
    return apps.get_model(getattr(settings, setting))


def get_kind_policy_model():
    """The kind policy model: `ISIK_NOTIFICATIONS_KIND_POLICY_MODEL`'s, else isik's."""
    return _model(KIND_POLICY_MODEL)


def get_notice_model():
    """The notice model: `ISIK_NOTIFICATIONS_NOTICE_MODEL`'s, else isik's."""
    return _model(NOTICE_MODEL)


def get_fan_out_shard_model():
    """The fan-out shard model: `ISIK_NOTIFICATIONS_FAN_OUT_SHARD_MODEL`'s, else isik's."""
    return _model(FAN_OUT_SHARD_MODEL)


def get_delivery_model():
    """The delivery model: `ISIK_NOTIFICATIONS_DELIVERY_MODEL`'s, else isik's."""
    return _model(DELIVERY_MODEL)


def get_webhook_delivery_model():
    """The webhook delivery model: `ISIK_NOTIFICATIONS_WEBHOOK_DELIVERY_MODEL`'s, else isik's."""
    return _model(WEBHOOK_DELIVERY_MODEL)


def get_email_delivery_model():
    """The email delivery model: `ISIK_NOTIFICATIONS_EMAIL_DELIVERY_MODEL`'s, else isik's."""
    return _model(EMAIL_DELIVERY_MODEL)


def get_inbox_delivery_model():
    """The inbox delivery model: `ISIK_NOTIFICATIONS_INBOX_DELIVERY_MODEL`'s, else isik's."""
    return _model(INBOX_DELIVERY_MODEL)


def get_delivery_attempt_model():
    """The delivery attempt model: `ISIK_NOTIFICATIONS_DELIVERY_ATTEMPT_MODEL`'s, else isik's."""
    return _model(DELIVERY_ATTEMPT_MODEL)
