"""The test project's inbound sources: another isik project's webhooks, signed as isik signs them, and a sender
naming no event id."""

from datetime import timedelta

from isik.django.apps.inbound.choices import PayloadKept
from isik.django.apps.inbound.sources import IsikSignature, Unidentified, declare_source, handles


SECRET = "acme-test-secret"
# What each handler was given, for a test to read.
HANDLED = []


declare_source(
    "acme",
    verifier=IsikSignature(header="X-Acme-Signature", secrets=lambda request: [SECRET], tolerance=timedelta(minutes=5)),
    event_id=lambda request, payload: payload["id"],
    event_type=lambda request, payload: payload["kind"],
)
declare_source(
    "pinger",
    verifier=IsikSignature(
        header="X-Pinger-Signature", secrets=lambda request: [SECRET], tolerance=timedelta(minutes=5)
    ),
    event_id=Unidentified(reason="the pinger posts a heartbeat with no id, and handling one twice changes nothing"),
    event_type=lambda request, payload: "ping",
    payload_kept=PayloadKept.UNTIL_HANDLED,
)


@handles("acme", "invoice_voided", "invoice_paid")
def invoice(event):
    HANDLED.append(("invoice", event.event_id))


@handles("pinger")
def ping(event):
    HANDLED.append(("ping", event.pk))
