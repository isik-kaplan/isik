# inbound

`pip install isik[inbound]`. Postgres and Celery required.

Webhooks a project receives from its senders. Each request is verified, read, written down as an event - once:
a sender's resend of an event already received is answered and dropped - and answered at once. A Celery task
then hands the event to its handler, inside the transaction that marks it handled, and tries it again on a
backoff when the handler fails. The row is the queue: a task is only a hint, and `work()` asks again for what a
lost task left.

isik does the receiving. What's each sender's is the project's: how it signs, where its event id and type are,
and the handlers.

## Setup

```python
INSTALLED_APPS = [..., "isik.django.apps.inbound"]

ISIK = {"DJANGO": {"INBOUND": {
    "SOURCES": "billing.inbound_sources",      # the module declaring the sources and handlers
    "BACKOFF": (60, 300, 1800, 7200),          # seconds after each failed attempt, the last repeating
    "STRANDED_AFTER": timedelta(minutes=10),   # how long a worker may hold an event before it's taken back
}}}

urlpatterns = [path("webhooks/", include(inbound_urls()))]   # isik.django.apps.inbound.views - POST /webhooks/<source>/

# celery.py - tasks.work is the scheduled task isik.inbound.work, with no schedule of isik's
override_scheduled_task("isik.inbound.work", every=timedelta(minutes=1))   # isik.django.scheduling
merge_beat_schedule(app)
```

`BACKOFF` and `STRANDED_AFTER` have no default - an event can't be tried again or taken back without them, and
how soon is the project's to say. `isik.E028` asks for both.

## Sources

A source is one sender, declared once:

```python
from isik.django.apps.inbound.sources import IsikSignature, declare_source, handles

declare_source(
    "acme",
    verifier=IsikSignature(header="X-Acme-Signature", secrets=lambda request: [settings.ACME_SECRET],
                           tolerance=timedelta(minutes=5)),
    event_id=lambda request, payload: payload["id"],
    event_type=lambda request, payload: payload["kind"],
)

@handles("acme", "invoice_voided", "invoice_paid")
def invoice(event):
    Invoice.objects.filter(number=event.payload["invoice"]).update(...)
```

- **`verifier`** - its `verify(request)` raises `Unverified` for a request the sender didn't sign; the request
  is answered 401 and nothing is written. `IsikSignature` verifies isik's own webhook signature
  (`t=<time>,v1=<hex HMAC-SHA256 of "<t>.<body>">`, see [channels](../notifications/channels.md#webhook)) - one isik
  project receiving another's webhooks - within `tolerance` of now, against any of `secrets(request)`, so a
  secret being rotated out still verifies. `SharedSecret(header=..., secrets=...)` verifies a sender sending a
  secret as it is - the value set in its dashboard, in `Authorization`, say, as RevenueCat does: the header's whole
  value against any of `secrets(request)`, in constant time. Any other sender's scheme is a class of the project's
  own with a `verify(request)`.
- **`event_id`** - f(request, payload) -> the sender's id for the event, which a resend repeats: one event is
  kept per source and id. A sender that names none is declared so, with a reason -
  `event_id=Unidentified(reason="...")` - and each of its requests is then written down and handled again; isik
  never falls back to hashing the body, which would make two separate, identical events one.
- **`event_type`** - f(request, payload) -> the kind of event, which picks its handler.
- **`parse`** - f(request) -> the payload; JSON unless given. A body it can't read, or one without the id or
  type, is answered 400.
- **`partition`** - f(request) -> the partition the request is for (see [Partitions](#partitions)).
- **`payload_kept`** - `PayloadKept.WITH_THE_EVENT` keeps what the sender posted for as long as the event is
  kept; `PayloadKept.UNTIL_HANDLED` drops it once the event is handled - the row stays, to dedupe resends and
  say what happened. What a sender posts is somebody's data whose shape the project didn't choose.

## Handlers

`@handles(source, *types)` declares a handler of those types of the source's events; `@handles(source)` one of
every type that has no handler of its own. A handler is called with the event, inside the transaction that marks
it handled - what it writes and the mark commit together. An exception rolls both back, records the error and
tries the event again after `BACKOFF`'s next delay, until `MAX_ATTEMPTS` (unset: for as long as it fails) gives
it up - abandoned, kept, and retried by hand with `pipeline.retry_event(event)`. An event no handler takes is
marked ignored.

A worker holding an event longer than `STRANDED_AFTER` is taken to have died: `work()` takes the event back, and
a finish from the worker after that is dropped, with what its handler wrote.

## The answer

| answer | when |
|---|---|
| 200 | written down, or a resend of an event written already |
| 400 `unreadable` | the body can't be read, or lacks the id or type |
| 400 `unidentified` | the id is empty |
| 401 `unverified` | the verifier refused it - its reason is logged, and answered |
| 404 `unknown_source` | no source of that name |
| 405 | anything but a POST |

## Partitions

In a partitioned project - one schema per tenant, say - nothing in a request says which tenant it's for but its
URL or host. A source's `partition` reads it from the request, and isik enters it with `IN_PARTITION` before
anything else: the verifier reads that partition's secrets, and the event is written there and handled there.
Written first and placed later, it would sit in another tenant's table.

```python
"INBOUND": {..., "PARTITIONS": "tenancy.schema_names", "IN_PARTITION": "django_tenants.utils.schema_context"}

declare_source("acme", ..., partition=lambda request: request.tenant.schema_name)
```

`isik.E028` refuses a source naming no partition in a partitioned project, and one naming a partition in a
project with none.

## Housekeeping

`tasks.work` - from the beat - in each partition: asks again for every due event, takes back what a dead worker
held, and, with `RETENTION` set, deletes events done (handled, ignored, given up on) longer ago than it. `BATCH`
caps how many of each it takes at a time.

## Settings

| key | default | |
|---|---|---|
| `SOURCES` | `None` | the module declaring the sources and handlers |
| `BACKOFF` | required | seconds after each failed attempt, the last repeating, plus up to a tenth of jitter |
| `STRANDED_AFTER` | required | how long a worker may hold an event before `work()` takes it back |
| `MAX_ATTEMPTS` | `None` | attempts before an event is given up on; `None` tries it for as long as it fails |
| `RETENTION` | `None` | how long a done event is kept; `None` keeps it |
| `BATCH` | `None` | the most events one `work()` takes back or prunes at a time |
| `PARTITIONS`, `IN_PARTITION` | `None` | the partitions, and entering one - both or neither |
| `EVENT_MODEL` | `"isik_inbound.InboundEvent"` | the event model - a project's own subclass of `AbstractInboundEvent` |

## Owning the events' table

```python
from isik.django.apps.inbound.bases import AbstractInboundEvent

class InboundEvent(AbstractInboundEvent):
    class Meta:
        db_table = "inbound_event"

ISIK = {"DJANGO": {"INBOUND": {..., "EVENT_MODEL": "billing.InboundEvent"}}}
```

Its rules - one event per source and id, and the indexes `work()` reads by - come with it, whatever its Meta.
