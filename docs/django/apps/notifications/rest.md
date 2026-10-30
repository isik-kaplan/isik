# REST views

`isik.django.apps.notifications.drf` - five viewsets to route, or ignore. Each is held to isik's write
guards (an `Idempotency-Key`, and `If-Match` on a write to one object), with the exemptions its own
writes earn, so `ATOMIC_REQUESTS` and an idempotency claims app are needed, as for any isik viewset.

```python
router.register("notifications/kinds", NoticeKindViewSet, basename="notice-kind")
router.register("notifications/inbox", InboxViewSet, basename="inbox")
router.register("notifications/subscriptions", SubscriptionViewSet, basename="subscription")
router.register("notifications/endpoints", MyEndpointViewSet, basename="endpoint")
router.register("notifications/deliveries", MyDeliveryViewSet, basename="delivery")
```

## `NoticeKindViewSet`

Every kind a person may get - the mandatory ones too - so a client renders the preferences from it alone.

| route | |
|---|---|
| `GET /` | the kinds: `{kind, label, description, policy, variants, channels, may_change, enabled, enabled_variants, forced, forced_enabled, forced_variants, forced_reason}` |
| `GET /{kind}/` | one |

`label` and `description` are in the request's language. `enabled` is what the caller gets of the kind now,
every variant - a force, else their own choice, else the policy, the very answer the fan-out gives - and
`enabled_variants` the same per variant; `forced_enabled` what staff forced it to, or null. A client draws
the preferences from these alone, with no policy logic of its own. `may_change` is false for a mandatory kind and
for one staff forced for the caller (every variant); `forced_variants` names the variants forced one by
one, and `forced_reason` is there when the force's source shows it. One function answers these for every
view, so the kinds and the subscriptions can't disagree.

Which kinds a caller sees is `KINDS_VISIBLE(request)` - every active kind by default - and which fields
are published is `NOTICE_KIND_FIELDS`, every one by default (name the rest to leave out `channels`, say).

## `InboxViewSet`

The caller's own items, newest first, each with its `title` and `message` rendered in the request's
language (see [labels and messages](kinds.md#what-a-person-reads-labels-and-messages)).

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
| `GET /` | them - a forced one with `forced: true`, `forced_enabled` what it's forced to (`enabled` stays the caller's own choice), and its `forced_reason` where the source shows it |
| `POST /` | `{kind, variant, enabled}` - sets one, with `subscribe()`, made or replacing the one before: `201` either way; no key needed |
| `DELETE /{id}/` | forgets one, so the policy decides again |

A mandatory kind, or a forced subscription, is refused with `409` and its code - `kind_mandatory`,
`subscription_forced` - in the `{detail, code}` body, so a client tells them apart.

Each view and action's docstring is what drf-spectacular shows your API's callers, so isik's are written for
them; each documents what it takes and answers exactly - `?unread=` on the inbox, no body on the `POST`
actions, `{marked}` from `read-all/`.

## `DeviceViewSet`

The caller's own devices - where pushes go.

| route | |
|---|---|
| `GET /` | them, with `is_active` false for one whose token its service said is dead |
| `POST /` | `{platform, token, environment, app_id}` - registers it, with `register_device()`: made, or, registered before by anyone, made the caller's and active again - `201` either way; no key needed |
| `DELETE /{id}/` | forgets one - at sign-out on that phone; no `If-Match` needed |

An app posts its token whenever it starts or the token changes: registering is idempotent, so neither a key
nor a version is asked for.

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
