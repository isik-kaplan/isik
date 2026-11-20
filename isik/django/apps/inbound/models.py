"""The inbound events isik ships, `InboundEvent` - swapped out by `ISIK['DJANGO']['INBOUND']['EVENT_MODEL']` for a
project owning them, see `bases.py`."""

from isik._internal.translation import gettext_lazy
from isik.django.apps.inbound.bases import AbstractInboundEvent


class InboundEvent(AbstractInboundEvent):
    class Meta(AbstractInboundEvent.Meta):
        db_table = "isik_inbound_event"
        swappable = "ISIK_INBOUND_EVENT_MODEL"
        verbose_name = gettext_lazy("inbound event")
        verbose_name_plural = gettext_lazy("inbound events")
