"""
The senders a project receives webhooks from - each declared once as a source - and the handlers its events go to:

    from isik.django.apps.inbound.sources import IsikSignature, declare_source, handles

    declare_source(
        "acme",
        verifier=IsikSignature(header="X-Acme-Signature", secrets=lambda request: [ACME_SECRET],
                               tolerance=timedelta(minutes=5)),
        event_id=lambda request, payload: payload["id"],
        event_type=lambda request, payload: payload["kind"],
    )

    @handles("acme", "invoice_voided")
    def void(event):
        ...

What's the sender's is the project's: how it signs (a verifier - `IsikSignature` for one isik project receiving
another's webhooks, a project's own for any other scheme), where its event id and type are, and the partition a
request is for. isik verifies, dedupes, writes down, answers and hands each event to its handler.
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from importlib import import_module

from django.core.exceptions import ImproperlyConfigured

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption
from isik.django.apps.inbound import conf
from isik.django.apps.inbound.choices import PayloadKept


class Unverified(Exception):
    """Why a request isn't one its source sent - a verifier raises it, and the request is answered 401."""


class Unidentified(
    Exemption,
    rule="isik.django.inbound.unidentified-events",
    why=(
        "A source naming no event id can't tell a sender's resend from a new event, so each one is written down and "
        "handled again."
    ),
):
    """Why a source's events carry no id to dedupe by - its `event_id`, in place of the function reading one."""


class IsikSignature:
    """
    Verifies isik's own webhook signature - `t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">`, a `v1=` by each
    of the sender's secrets - in `header`: one `v1=` matching one of `secrets(request)` within `tolerance` of now.
    `secrets` is a function of the request, so a partitioned project reads the partition's own.
    """

    def __init__(self, *, header, secrets, tolerance):
        self.header, self.secrets, self.tolerance = header, secrets, tolerance

    def verify(self, request):
        value = request.headers.get(self.header)
        if not value:
            raise Unverified(_("The request carries no %(header)s.") % {"header": self.header})
        parts = [part.partition("=") for part in value.split(",")]
        stamps = [given for name, _sep, given in parts if name == "t"]
        signatures = [given for name, _sep, given in parts if name == "v1"]
        if len(stamps) != 1 or not stamps[0].isdigit() or not signatures:
            raise Unverified(_("%(header)s isn't t=<time>,v1=<signature>.") % {"header": self.header})
        if abs(time.time() - int(stamps[0])) > self.tolerance.total_seconds():
            raise Unverified(_("%(header)s was signed too long ago, or ahead of now.") % {"header": self.header})
        signed = f"{stamps[0]}.".encode() + request.body
        expected = [
            hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest() for secret in self.secrets(request) if secret
        ]
        if not any(hmac.compare_digest(want, given) for want in expected for given in signatures):
            raise Unverified(_("%(header)s matches none of the source's secrets.") % {"header": self.header})


def json_payload(request):
    """The request's body as JSON - a source's `parse` unless it names another. A ValueError is a 400."""
    return json.loads(request.body)


@dataclass(frozen=True)
class Source:
    """One declared sender. Built by `declare_source()`, not by hand."""

    name: str
    verifier: object
    event_id: object
    event_type: object
    partition: object = None
    parse: object = json_payload
    payload_kept: PayloadKept = PayloadKept.WITH_THE_EVENT

    @property
    def identifies_events(self):
        """Whether its events carry an id a resend repeats - all but a source declared `Unidentified`."""
        return not isinstance(self.event_id, Unidentified)


# Every declared source, by name; every declared handler, by (source, event type) - the type None for one taking
# every type of its source that has no handler of its own.
sources = {}
handlers = {}


def declare_source(
    name,
    *,
    verifier,
    event_id,
    event_type,
    partition=None,
    parse=json_payload,
    payload_kept=PayloadKept.WITH_THE_EVENT,
):
    """
    Declares the source `name`, received at `<name>/` under `inbound_urls()`:

    - `verifier` - its `verify(request)` raises `Unverified` for a request the sender didn't sign.
    - `event_id` - f(request, payload) -> the sender's id for the event, which a resend repeats; or
      `Unidentified(reason=...)` for a sender naming none, whose resends are then handled again.
    - `event_type` - f(request, payload) -> the kind of event, which picks its handler.
    - `partition` - f(request) -> the partition the request is for, entered before anything is read or written:
      the verifier reads its secrets there, and the event is written there.
    - `parse` - f(request) -> the payload, JSON by default; a ValueError is answered 400.
    - `payload_kept` - `PayloadKept.UNTIL_HANDLED` drops the payload once the event is handled, the row kept.

    Returns the `Source`.
    """
    if name in sources:
        raise ImproperlyConfigured(_("A source called %(name)s is declared already.") % {"name": name})
    declared = Source(
        name=name,
        verifier=verifier,
        event_id=event_id,
        event_type=event_type,
        partition=partition,
        parse=parse,
        payload_kept=payload_kept,
    )
    sources[name] = declared
    return declared


def handles(source, *event_types):
    """
    Declares the decorated function the handler of `source`'s `event_types` - or, naming none, of every type of
    `source` no other handler takes. It's called with the event, inside the transaction marking it handled: what it
    writes and the mark commit together, and an exception rolls both back and tries it again later.
    """

    def register(handler):
        for event_type in event_types or (None,):
            if (source, event_type) in handlers:
                raise ImproperlyConfigured(
                    _("%(source)s's %(type)s events have a handler already.")
                    % {"source": source, "type": event_type or _("every")}
                )
            handlers[(source, event_type)] = handler
        return handler

    return register


def handler_for(source, event_type):
    """The handler of `source`'s `event_type` events - its own, else the source's for every type - or None."""
    return handlers.get((source, event_type), handlers.get((source, None)))


def import_sources_module():
    """Imports `SOURCES`, the module declaring the project's sources and handlers - where it's named."""
    path = conf.setting("SOURCES")
    if path:
        import_module(path)
