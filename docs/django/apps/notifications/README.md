# notifications

`pip install isik[notifications]`. Postgres and Celery required.

A notice is written by **the database**, inside the transaction that made the change it describes: a
trigger compiled from the kind's declaration inserts it. A raw `UPDATE` in psql writes one too, so
nothing can forget to notify. A Celery task then fans each notice out into deliveries on the in-app
inbox, email, webhook and push channels, and each delivery is tried until it's sent.

- [kinds.md](kinds.md) - declaring a kind: `notifies()`, `declare_kind()`, `raise_notice()`, variants
- [audiences.md](audiences.md) - who a kind reaches, its policy, and subscriptions
- [channels.md](channels.md) - inbox, email, webhooks (the wire format and signing), push to phones, your own channel
- [partitions.md](partitions.md) - tenants, schemas, or any other split of your data
- [operations.md](operations.md) - the tasks, the beat schedule, retries, pruning, health, `manage.py notices`, tests
- [rest.md](rest.md) - the optional REST views: the inbox, subscriptions, devices and endpoints

## Setup

Two apps, installed together:

```python
INSTALLED_APPS = [
    ...,
    "pgtrigger",
    "pghistory",
    "isik.django.apps.common",
    "isik.django.apps.notifications",           # the ledger: notices, deliveries, attempts
    "isik.django.apps.notifications.audience",  # the rows naming people: subscriptions, inbox, endpoints, devices
]

ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Acme"}   # only needed once a kind is sent by webhook
```

Then declare a kind on the model it watches, write its email templates if it's sent by email, call
`merge_beat_schedule(app)` in your `celery.py` (see [operations.md](operations.md)), and run `migrate` - which
also writes each kind's policy row. A project with no webhook kinds sets no `ISIK_NOTIFICATIONS` at all.

Your `transaction=True` tests keep working: the ledger refuses `TRUNCATE`, which a test's flush uses, and
isik's pytest plugin lets that flush through on its own (see [operations.md](operations.md#tests)).

```python
from isik.django.apps.notifications.audiences import Field
from isik.django.apps.notifications.kinds import Policy, declare_kind, notifies  # Scope and Source too

INVOICE_VOIDED = declare_kind("invoice_voided")  # in one module of every kind - see kinds.md

@notifies(
    INVOICE_VOIDED,
    on=pgtrigger.Update,
    condition=pgtrigger.Q(old__voided_at__isnull=True, new__voided_at__isnull=False),
    snapshot=["number", "customer"],
    audience=Field("customer"),
    policy=Policy.MANDATORY,
)
class Invoice(BaseModel):
    ...
```

## Two apps, and why

The split is "does the table name a person". The ledger (`NoticeKindPolicy`, `Notice`, `FanOutShard`,
`Delivery` and its channel children, `DeliveryAttempt`) holds no key to a user. The audience
(`Subscription`, `InboxItem`, `Endpoint`, `EndpointKind`) is defined by having an owner. A project
splitting its data by tenant installs the ledger once and the audience per tenant - see
[partitions.md](partitions.md). A check (`isik.E014`) refuses the audience app without the ledger.

## Owning every model

isik ships a working model for each of the fourteen, and every one is swappable: subclass its abstract base,
point its setting at your subclass, and isik's table is never made - yours is, in your app, with your
migrations and your admin, and isik's mechanism runs over it.

| model | abstract base | setting |
|---|---|---|
| `NoticeKindPolicy` | `bases.AbstractNoticeKindPolicy` | `ISIK_NOTIFICATIONS_KIND_POLICY_MODEL` |
| `Notice` | `bases.AbstractNotice` | `ISIK_NOTIFICATIONS_NOTICE_MODEL` |
| `FanOutShard` | `bases.AbstractFanOutShard` | `ISIK_NOTIFICATIONS_FAN_OUT_SHARD_MODEL` |
| `Delivery` | `bases.AbstractDelivery` | `ISIK_NOTIFICATIONS_DELIVERY_MODEL` |
| `WebhookDelivery` | `bases.AbstractWebhookDelivery` | `ISIK_NOTIFICATIONS_WEBHOOK_DELIVERY_MODEL` |
| `EmailDelivery` | `bases.AbstractEmailDelivery` | `ISIK_NOTIFICATIONS_EMAIL_DELIVERY_MODEL` |
| `InboxDelivery` | `bases.AbstractInboxDelivery` | `ISIK_NOTIFICATIONS_INBOX_DELIVERY_MODEL` |
| `PushDelivery` | `bases.AbstractPushDelivery` | `ISIK_NOTIFICATIONS_PUSH_DELIVERY_MODEL` |
| `DeliveryAttempt` | `bases.AbstractDeliveryAttempt` | `ISIK_NOTIFICATIONS_DELIVERY_ATTEMPT_MODEL` |
| `Subscription` | `audience.bases.AbstractSubscription` | `ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL` |
| `InboxItem` | `audience.bases.AbstractInboxItem` | `ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL` |
| `Endpoint` | `audience.bases.AbstractEndpoint` | `ISIK_NOTIFICATIONS_ENDPOINT_MODEL` |
| `EndpointKind` | `audience.bases.AbstractEndpointKind` | `ISIK_NOTIFICATIONS_ENDPOINT_KIND_MODEL` |
| `Device` | `audience.bases.AbstractDevice` | `ISIK_NOTIFICATIONS_DEVICE_MODEL` |

```python
# notifications/models.py - your app
from isik.django.apps.notifications import bases

class Notice(bases.AbstractNotice):
    pass

class Delivery(bases.AbstractDelivery):
    pass
# ... and the rest

# settings.py
ISIK_NOTIFICATIONS_NOTICE_MODEL = "notifications.Notice"
ISIK_NOTIFICATIONS_DELIVERY_MODEL = "notifications.Delivery"
```

The ledger's bases carry every field, and their keys point at the configured models, so the subclasses
stay empty and key to each other. The audience's bases declare every field but `user` and `kind` (and
`EndpointKind.endpoint`), which your model declares - so it can key them however your database needs. Each
concrete subclass gets its rules when it's prepared, whatever its Meta inherits: the ledger's triggers (a
notice frozen and stamped once, attempts append-only, no `TRUNCATE`, a kind never deleted or renamed), its
unique constraints and indexes, and the audience's `VariantGuard`. Every trigger reads its table and
columns off the configured model, so they land in your migrations, naming your tables. Read the models with
the getters - `get_notice_model()` and its siblings in `isik.django.apps.notifications.swapping`, and
`get_subscription_model()` and its siblings in `...audience`. Reach for the getter everywhere, your tests
included: `from isik.django.apps.notifications.models import Notice` names isik's default, which stops
being there the moment it's swapped.

**Keys.** `Delivery`, `FanOutShard` and `DeliveryAttempt` are keyed by `new_primary_key()`, so
`ISIK_PRIMARY_KEY_DEFAULT` governs them like every other isik model - random `uuid4` unless it's set, so a
project keying its own tables by `uuid7` sets it, or these three, its busiest append-only tables, get
random keys. `Notice` is the exception - its trigger
writes it in SQL, which no Python key function reaches, so it's a sequence. To key notices otherwise, give
your `Notice` an `id` the database can fill:

```python
class Notice(bases.AbstractNotice):
    id = models.UUIDField(primary_key=True, default=uuid7, db_default=models.Func(function="uuidv7"))
```

`uuidv7()` is Postgres 18 and later; before it, use `gen_random_uuid()` or a function of your own. No check
can see a function the database lacks - the first notice its trigger writes fails.

Own the policy model, and own the three models its guard reads it from too - `Subscription`, `InboxItem`
and `EndpointKind`: isik's shipped migration guards its own against isik's policy table. `isik.E014`
refuses one kept. (`Endpoint` reads no kind, and may stay isik's.)

Swap when the tables are first made: an empty subclass and a setting then, a data migration between two
tables after a deployment. Keep both apps installed either way - they carry the mechanism, and their own
tables are simply never made.

## What is guaranteed

| hop | guarantee | held by |
|---|---|---|
| change -> notice | exactly once | one transaction: the change and its notice commit together |
| notice -> deliveries | exactly once | `SELECT ... FOR UPDATE SKIP LOCKED`, the write-once `fanned_out_at`, `unique(notice, channel, target_key)` |
| delivery -> inbox | exactly once | one database: the item is keyed by its delivery |
| delivery -> webhook, email | at least once | nothing on the sending side can tell "never arrived" from "arrived, answer lost" |

So a webhook receiver dedupes on the `Idempotency-Key` - the delivery's id, the same on every attempt -
and an email carries a `Message-ID` taken from it.

The ledger protects itself: a notice is frozen once written (`fanned_out_at` aside, which is set once),
attempts are append-only, a policy row is never deleted, and `TRUNCATE` is refused on the notice and
attempt tables (`ProtectTruncate`, see [operations.md](operations.md) for tests). None of this stops a
superuser turning triggers off - it narrows accidents, not attackers.

## Settings

`ISIK_NOTIFICATIONS`, a dict - every key has a default, and a key it doesn't know is refused by
`isik.E014`. A hook is a callable or its dotted path.

| key | default | |
|---|---|---|
| `REQUIRE_DECLARED_KINDS` | False | refuse a bare name given to `notifies()` - every kind declared with `declare_kind()` |
| `PROJECT_NAME` | None | names the webhook signature header, `X-<PROJECT_NAME>-Signature` - required once a kind is sent by webhook, unless `SIGNATURE["header"]` is set |
| `SIGNATURE` | `{}` | `{"header": ...}` names the signature header outright |
| `CHANNELS` | `("inbox", "email", "webhook")` | the channels a kind may be sent on - `"push"` too, with `isik[push]` |
| `PUSH` | `{}` | `{"APNS": {...}, "FCM": {...}}` - the push services' credentials ([push](channels.md#push)) |
| `RECORD_ATTEMPTS` | `"failures"` | which attempts get a `DeliveryAttempt` row: `"all"`, `"failures"`, `"none"` |
| `MAX_ATTEMPTS` | 5 | tries before a delivery (or shard) is abandoned - in each run, a manual retry starting one |
| `BACKOFF` | `(60, 300, 1800, 7200, 21600)` | seconds to wait after each failure, the last repeating, plus up to a tenth of jitter |
| `RETRYABLE_STATUSES` | 5xx, 408, 425, 429 | the webhook answers worth trying again; any other is permanent |
| `ENDPOINT_TIMEOUT` | 10 | seconds a webhook's whole answer may take - and a push's |
| `DISABLE_AFTER` | 10 | failures in a row that park a webhook endpoint |
| `ALLOW_PRIVATE_TARGETS` | False | whether a webhook may reach a non-public address: `True`, or the networks it may |
| `WEBHOOK_PROXY` | None | the egress proxy webhooks go out through - the environment's is never used |
| `MAX_RESPONSE_BYTES` | 4096 | how much of a response body an attempt keeps |
| `REDACTOR` | None | `f(dict) -> dict`, run over an attempt's request and response before they're written |
| `EMAIL_SENDER` | templates by kind | `f(delivery, notice, address)` - see [channels.md](channels.md) |
| `EMAIL_ADDRESS` | the user's email field | `f(user) -> address or None` |
| `RECIPIENT_LANGUAGE` | the user's `language`, when set | `f(user) -> language or None` - see [channels.md](channels.md) |
| `PARTITIONS`, `IN_PARTITION`, `PARTITION_EXISTS`, `DEFAULT_PARTITION` | None | see [partitions.md](partitions.md) |
| `ATTEMPT_RETENTION`, `NOTICE_RETENTION` | 30 days, 90 days | None keeps them |
| `BATCH_SIZE` | 100 | notices one fan-out takes |
| `EMAIL_CONTEXT` | `None` | `f(delivery, notice) -> dict` every email the shipped senders render starts from, under the kind's own context |
| `ACTOR_CONTEXT_KEY` | `"user"` | the history context's key naming who acted - each notice's `actor_id`; `None` records no one ([who acted](channels.md#who-acted)) |
| `NOTICE_CONTEXT_KEYS` | `()` | the history context's keys each notice keeps in `context`, such as the actor's schema |
| `ENDPOINT_OWNER_FIELD` | `None` | the endpoint's lookup naming its owner, when endpoints are owned: each then hears only notices whose audience holds its owner ([webhook](channels.md#webhook)) |
| `ENDPOINTS_SHARED` | `None` | `SharedEndpoints(reason=...)`: why endpoints keyed to a user are still the deployment's (`isik.W008`) |
| `FAN_OUT_AFTER_TASKS` | `False` | start a fan-out after every Celery task that left a notice waiting - [without a beat](operations.md#without-a-beat) |
| `FAN_OUT_BY` | `None` | `ScheduledElsewhere(reason=...)`: what starts the fan-out where isik can't see it (`isik.W006`) |
| `SENDS_INLINE` | `None` | `SendsInline(reason=...)`: why sending webhooks inside the writing request is fine (`isik.W007`) |
| `STRANDED_AFTER` | 10 minutes | how long a delivery may stay sending, or a shard claimed, before it's taken back |
| `HOUSEKEEPING_BATCH` | 1000 | the most rows one reap or prune touches |
