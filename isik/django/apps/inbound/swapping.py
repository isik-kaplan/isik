"""
The inbound event model's swappable setting, and the getter reading it - `get_inbound_event_model()`. The setting
names an `"app_label.Model"`; unset, it names the model isik ships. A project owning the events' table subclasses
`AbstractInboundEvent` (`bases.py`) and names the subclass in `ISIK['DJANGO']['INBOUND']['EVENT_MODEL']`.
"""

from django.apps import apps
from django.conf import settings

from isik.django.conf import give_swappable_settings


EVENT_MODEL = "ISIK_INBOUND_EVENT_MODEL"

DEFAULTS = {EVENT_MODEL: "isik_inbound.InboundEvent"}


def set_defaults():
    """Gives the swappable setting what `ISIK['DJANGO']['INBOUND']` names, else its default."""
    give_swappable_settings("INBOUND", DEFAULTS)
    for name, default in DEFAULTS.items():
        if not hasattr(settings, name):
            setattr(settings, name, default)


def get_inbound_event_model():
    """The inbound event model: `ISIK_INBOUND_EVENT_MODEL`'s, else isik's."""
    return apps.get_model(getattr(settings, EVENT_MODEL))
