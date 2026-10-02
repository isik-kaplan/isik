# idempotency

`pip install isik[drf]`. Postgres and `ATOMIC_REQUESTS` required.

A caller sends `Idempotency-Key: <uuid>` with a state-changing request. A retry with the same key
and the same request gets the first response back, status and body, instead of doing the work twice.

- [mixin.md](mixin.md) - `IdempotencyMixin`, what a view honours and how to configure it
- [claims.md](claims.md) - the claim models, choosing one, `claim_idempotency_key()`, body codecs
- [fingerprint.md](fingerprint.md) - `request_fingerprint()`, `canonical_body()`, custom normalizers

## Setup

Install **one** of the two claim apps. Each ships one model and its migration, so the claim you
don't use never gets a table:

```python
INSTALLED_APPS = [
    ...,
    "isik.django.apps.idempotency.by_reference",  # replays by re-serializing the row - stores no body
    # or "isik.django.apps.idempotency.with_body", which keeps the response body
]

DATABASES = {"default": {..., "ATOMIC_REQUESTS": True}}
```

Then mix `IdempotencyMixin` into your viewsets:

```python
from isik.django.apps.idempotency.drf import IdempotencyMixin

class WidgetViewSet(IdempotencyMixin, BaseModelViewSet):
    ...
```

With django-tenants, put the claim app in `TENANT_APPS` (or both lists) to get one table per schema.

## The contract

| Request                                          | Answer                                               |
|--------------------------------------------------|------------------------------------------------------|
| covered method, no key (key required)            | 400 `{"Idempotency-Key": ["This header is required."]}` |
| key that isn't a UUID                            | 400 `{"Idempotency-Key": ["Must be a UUID."]}`       |
| anonymous caller                                 | `NotAuthenticated` (401/403)                         |
| first use of a key                               | the handler's own response                           |
| same key, same request                           | the first response, plus `Idempotent-Replayed: true` |
| same key, different method/path/query/body       | 422 `idempotency_key_reused`                         |
| same key on an action declared no-replay         | 409 `idempotency_key_not_replayable`                 |
| first request still running past `IDEMPOTENCY_LOCK_TIMEOUT` | 409 `idempotency_key_in_flight`           |
| replay by reference, row since deleted           | 410 `idempotent_replay_gone`                         |

A key is scoped to the caller (`request.user`). Two callers who pick the same key never meet.

## How it works

The claim row is inserted **inside the request's transaction**, after authentication and
permissions and before the handler runs, against a `UNIQUE (claimed_by, key)` index. A concurrent
retry's insert **waits** on that index until the first transaction ends. If the first request
committed, the retry reads its claim and replays it. If it rolled back, the retry's insert goes
through and it does the work for real.

So the request transaction is what makes this work. The usual design commits a claim before the
work and treats the request transaction as an obstacle. Doing it inside the transaction means:

- **No in-flight state.** A claim exists only if its request committed.
- **Nothing to reap.** A crash, an exception or a killed worker aborts the transaction, and the claim
  goes with it. The retry is a clean first attempt.
- **No clock.** No expiry, no sweep, no stuck-claim interval to guess. Claims are kept until you
  delete them, and deleting old ones is always safe.

**A failure doesn't spend the key.** DRF's exception handler calls `set_rollback()` for every
`APIException`, so a raised 400/403/404 rolls the claim back with the rest of the request. A handler
that *returns* a response of 400 or above has its claim deleted instead, so its key isn't spent
either, while whatever work it did is kept, as it intended. In both cases a retry is a real attempt
rather than a replayed refusal.

A replay goes through `initial()` like any request, so the view's permissions run again. A caller
who has lost access since the first request is refused instead of answered from the record.

## What stays with you

- Which handlers are covered. `IdempotencyMixin` covers `POST` by default. Leave the mixin off views
  whose responses you don't own (a third-party auth library's endpoints, say).
- A system check that fails the build for an unprotected handler, if you want one. Which views count
  as "should be protected" is a project decision.
- Publishing the header in your schema (`OpenApiParameter`), so typed clients send it.

The client half matters as much as the server half. A key minted per call protects nothing: a user
who submits twice makes two calls with two keys. The key belongs to the *attempt*. Keep it while
the payload stays the same, and mint a new one when the payload changes or after a success.
