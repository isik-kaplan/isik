# REST views

`isik.django.apps.notifications.drf` - four viewsets to route, or ignore. Each is held to isik's write
guards (an `Idempotency-Key`, and `If-Match` on a write to one object), with the exemptions its own
writes earn, so `ATOMIC_REQUESTS` and an idempotency claims app are needed, as for any isik viewset.

```python
router.register("notifications/inbox", InboxViewSet, basename="inbox")
router.register("notifications/subscriptions", SubscriptionViewSet, basename="subscription")
router.register("notifications/endpoints", MyEndpointViewSet, basename="endpoint")
router.register("notifications/deliveries", MyDeliveryViewSet, basename="delivery")
```

## `InboxViewSet`

The caller's own items, newest first.

| route | |
|---|---|
| `GET /` | the inbox; `?unread=true` for what's unread |
| `GET /{id}/` | one item |
| `POST /{id}/read/` | marks it read - no key or `If-Match` needed |
| `POST /read-all/` | marks every item read: `{"marked": n}` |

## `SubscriptionViewSet`

The caller's own subscriptions.

| route | |
|---|---|
| `GET /` | them |
| `GET /kinds/` | every kind the caller can choose about - not mandatory, not retired - with its policy and variants |
| `POST /` | `{kind, variant, enabled}` - sets one, with `subscribe()`, made or replacing the one before: `201` either way; no key needed |
| `DELETE /{id}/` | forgets one, so the policy decides again |

Each view and action's docstring is what drf-spectacular shows your API's callers, so isik's are written for
them; each documents what it takes and answers exactly - `?unread=` on the inbox, no body on the `POST`
actions, `{marked}` from `read-all/`, a list of `{kind, policy, variants}` from `kinds/`.

## Whose endpoints, and whose deliveries

Whose endpoints a caller sees - and so whose deliveries - is yours to say, and both views refuse to
guess. One line does it when the endpoint has a field naming its owner:

```python
class MyEndpointViewSet(EndpointViewSet):
    owner_field = "user"                     # filtered on, and given to a new endpoint

class MyDeliveryViewSet(DeliveryViewSet):
    owner_field = "user"
```

`owner_field` scopes what a caller sees through the API; `ENDPOINT_OWNER_FIELD` scopes what the channel
sends each endpoint. Set them alike - `isik.E014` holds them to it - or a caller's endpoints receive other
owners' notices (see [owned endpoints](channels.md#webhook)).

`owner(request)` is the owner - `request.user` by default; override it for another
(`request.user.organization`). Or override `get_queryset()` (and `perform_create()`) outright. isik's own
`Endpoint` has no owner, deliberately - that's what swapping it is for.

Each viewset's `model` is the configured one - the project's, when it's swapped - read with no request,
so isik's checks (`isik.E002`) and drf-spectacular find it, and the schema types each `{id}`. A
`get_queryset()` of your own that reads the request keeps that with `@none_during_schema_generation`
(from `isik.django.drf.viewsets.schema_generation`), as isik's do.

## `EndpointViewSet`

| route | |
|---|---|
| `GET`, `POST`, `GET /{id}/`, `PATCH`/`PUT`/`DELETE /{id}/` | the endpoints, each with `kinds: [{kind, variant}]` |
| `POST /{id}/rotate-secret/` | a new secret, the old one signing beside it |
| `POST /{id}/finish-rotation/` | drops the old one |
| `POST /{id}/unpark/` | sends to a parked endpoint again |

An endpoint's version is its `updated_at`, which its kinds move too - so each write to one names it with
`If-Match`. Its secret is in the response that creates it, and `rotate-secret`'s - never anywhere else,
and neither is replayed. `kinds` naming a kind twice, a variant it doesn't declare, or one kind both for
every variant and for some, is refused with a message saying so.

## `DeliveryViewSet`

The webhook deliveries to the caller's endpoints, newest first, each with its attempts.

| route | |
|---|---|
| `GET /`, `GET /{id}/` | them |
| `POST /{id}/retry/` | sends an abandoned one again (`retry_delivery()`) - a 400 saying why when it can't be, such as a parked endpoint |
