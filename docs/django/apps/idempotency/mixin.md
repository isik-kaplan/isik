# IdempotencyMixin

`isik.django.apps.idempotency.drf.IdempotencyMixin` - mix into any DRF `GenericAPIView`, viewset or
not.

```python
class InstallationViewSet(IdempotencyMixin, BaseModelViewSet):
    model = Installation
    endpoint = "installations"
    serializer_class = InstallationSerializer
    idempotency_exempt_actions = {"preview": "validates a configuration and changes nothing"}
    idempotency_no_replay_actions = {"rotate_secret": "returns a client secret that is shown once"}
```

```
POST /installations/  Idempotency-Key: 0192f5a4-...   -> 201 {"id": 7, ...}
POST /installations/  Idempotency-Key: 0192f5a4-...   -> 201 {"id": 7, ...}   Idempotent-Replayed: true
```

## Attributes

| Attribute                        | Default            | Meaning |
|----------------------------------|--------------------|---------|
| `idempotent_methods`             | `("POST",)`        | Which methods are covered. `PUT`/`PATCH`/`DELETE` on a detail route are idempotent already. |
| `idempotency_key_required`       | `True`             | A covered request without the header is a 400. `False` honors a key only when one is sent. |
| `idempotency_exempt_actions`     | `{}`               | `{action: reason}` - actions the key is never asked of, e.g. a POST that changes nothing. |
| `idempotency_no_replay_actions`  | `{}`               | `{action: reason}` - actions whose response must never be stored. |
| `idempotency_normalize`          | `None`             | A function of the request that decides what its body is compared by. See [fingerprint.md](fingerprint.md). |
| `idempotency_claim_model`        | `None`             | The claim model for this view, or `"app_label.Model"`. See [claims.md](claims.md). |
| `idempotency_header`             | `"Idempotency-Key"` | The request header holding the key. |
| `idempotency_replayed_header`    | `"Idempotent-Replayed"` | The response header marking a replay. |

Both `{action: reason}` dicts need a non-blank reason for every action, or the class raises
`ImproperlyConfigured` when it's defined. An exemption should say what it exempts.

`idempotency_normalize` can also be set per action, because DRF hands an `@action`'s extra keyword
arguments to the view:

```python
@action(detail=False, methods=["post"], idempotency_normalize=without_nonce)
def sign(self, request): ...
```

A function set on the class stays a plain function of the request. It isn't bound as a method.

## Actions that can't be replayed

An action whose response hands out something shown once, like a secret or a token, goes in
`idempotency_no_replay_actions`. Its first request is answered normally and its claim keeps **only
the status**. That holds even with a body-keeping claim, so the secret doesn't get a second lifetime
in the claim table. A retry is refused with 409. The caller recovers through something they already
hold, such as rotating the secret or re-issuing the token.

A different request with the same key still gets 422, which is the more useful diagnosis.

## Rules for handlers

- **Report failures by raising.** A raised `APIException` rolls back the claim with everything else.
  A returned 4xx also releases the claim, but keeps the handler's work.
- **By reference** (`IdempotencyClaim`), a covered action's response must be the view's own
  `get_serializer_class()` over one of `get_queryset()`'s rows, or have no body. Anything else raises
  `ImproperlyConfigured` when the response is recorded. That happens inside the transaction, so the
  work rolls back with it and nothing is left half-recorded. Declare the action no-replay, or use the
  body-keeping claim.

## Hooks

- `is_idempotent(request)` - whether the request is covered.
- `get_idempotency_key(request)` - the key as a `UUID`, or `None` when it's optional and not sent.
- `get_idempotency_owner(request)` - who the claim is scoped to. `request.user`, and
  `NotAuthenticated` for an anonymous caller.
- `idempotency_action` - the action being served, or `None` on a view that isn't a viewset.
