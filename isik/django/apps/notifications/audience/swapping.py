"""
The audience models' swappable settings, and the getters reading them - `get_subscription_model()` and
its siblings. Each setting names an `"app_label.Model"`; unset, it names the model isik ships.

A migration naming a swappable model reads its setting by name (`settings.ISIK_NOTIFICATIONS_...`), as
one naming the user model reads `settings.AUTH_USER_MODEL` - so each is given its default here, when the
audience app loads, for a project that sets none of them.
"""

from django.apps import apps
from django.conf import settings


SUBSCRIPTION_MODEL = "ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL"
INBOX_ITEM_MODEL = "ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL"
ENDPOINT_MODEL = "ISIK_NOTIFICATIONS_ENDPOINT_MODEL"
ENDPOINT_KIND_MODEL = "ISIK_NOTIFICATIONS_ENDPOINT_KIND_MODEL"

DEFAULTS = {
    SUBSCRIPTION_MODEL: "isik_notifications_audience.Subscription",
    INBOX_ITEM_MODEL: "isik_notifications_audience.InboxItem",
    ENDPOINT_MODEL: "isik_notifications_audience.Endpoint",
    ENDPOINT_KIND_MODEL: "isik_notifications_audience.EndpointKind",
}


def set_defaults():
    """Gives each swappable setting the project leaves unset its default."""
    for name, default in DEFAULTS.items():
        if not hasattr(settings, name):
            setattr(settings, name, default)


def endpoint_model_name():
    return getattr(settings, ENDPOINT_MODEL)


def _model(name):
    # set_defaults() has given every setting a value by the time anything asks.
    return apps.get_model(getattr(settings, name))


def get_subscription_model():
    """The subscription model: `ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL`'s, else isik's."""
    return _model(SUBSCRIPTION_MODEL)


def get_inbox_item_model():
    """The inbox item model: `ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL`'s, else isik's."""
    return _model(INBOX_ITEM_MODEL)


def get_endpoint_model():
    """The endpoint model: `ISIK_NOTIFICATIONS_ENDPOINT_MODEL`'s, else isik's."""
    return _model(ENDPOINT_MODEL)


def get_endpoint_kind_model():
    """The endpoint kind model: `ISIK_NOTIFICATIONS_ENDPOINT_KIND_MODEL`'s, else isik's."""
    return _model(ENDPOINT_KIND_MODEL)
