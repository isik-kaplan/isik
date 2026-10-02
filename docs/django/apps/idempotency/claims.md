# claims

`isik.django.apps.idempotency.claims`.

## The models

```
AbstractBaseIdempotencyClaim         claimed_by, key, fingerprint, status, claimed_at
├── AbstractIdempotencyClaim         + object_id   -> IdempotencyClaim          (by_reference app)
└── AbstractIdempotencyClaimWithBody + body        -> IdempotencyClaimWithBody  (with_body app)
```

The two concrete models live in two apps, `isik.django.apps.idempotency.by_reference` and
`isik.django.apps.idempotency.with_body`. Each app ships its own migration, so the body column
exists only in a project that chose to keep bodies. A nullable column would invite someone to start
filling it. A missing column is a decision.

- `claimed_by` - FK to `AUTH_USER_MODEL`, `CASCADE`.
- `key` - a `UUIDField`. Any UUID version is accepted.
- `fingerprint` - `request_fingerprint()` of the request that spent the key.
- `status` - the status it was answered with. Null only while its request is being served, which no
  other transaction can see.
- `claimed_at` - when the claim was made. Useful for deleting old claims. Nothing expires on its own.

`UNIQUE (claimed_by, key)` is the whole mechanism - see [README.md](README.md#how-it-works).

### By reference: `IdempotencyClaim`

Stores the pk of the row the response serialized, never the body. A replay re-fetches that row
through the view's `get_queryset()`, runs `check_object_permissions()` on it, and serializes it
with `get_serializer()`. So:

- Nothing is at rest that isn't already in the table it came from. There's no second copy of
  anything sensitive and nothing to encrypt.
- A replay shows the row **as it is now**, not as it was.
- A row that's gone since is answered with 410. So is one `get_queryset()` no longer returns.

### With body: `IdempotencyClaimWithBody`

Stores `response.data` through a codec. A replay sends it back exactly as it was, whatever the
handler returned. Use this when responses aren't serialized rows.

## Choosing the model

`get_claim_model(model=None)` resolves the model in this order:

1. `model`, which is the view's `idempotency_claim_model`: a model class or `"app_label.Model"`.
2. The `IDEMPOTENCY_CLAIM_MODEL` setting.
3. Whichever one of the two claim apps is installed.

If that leaves no model, or both apps are installed with nothing chosen, it raises
`ImproperlyConfigured`.

## Your own claim

Subclass one of the abstract claims in your own app and point `IDEMPOTENCY_CLAIM_MODEL` at it. You
can add a column, a different owner FK, or your own replay. A claim from scratch subclasses
`AbstractBaseIdempotencyClaim` and implements:

- `keep_response(view, response)` - store what `replay()` needs, on `self`. `record()` saves it.
- `replay(view, request)` - return the `Response` a retry gets.

## Body codecs

The body goes through a codec: anything with `encode(data) -> str` and `decode(str) -> data`. The
claim uses its `body_codec` attribute if set, else the `IDEMPOTENCY_BODY_CODEC` setting (a codec, a
codec class, or a dotted path to either), else `JSONBodyCodec`, which uses DRF's JSON encoder.

Encrypting bodies at rest is a codec. With `cryptography`'s Fernet, say:

```python
from cryptography.fernet import MultiFernet, Fernet
from isik.django.apps.idempotency.claims import JSONBodyCodec

class EncryptedBodyCodec:
    def __init__(self):
        self.fernet = MultiFernet([Fernet(key) for key in settings.IDEMPOTENCY_BODY_KEYS])

    def encode(self, data):
        return self.fernet.encrypt(JSONBodyCodec().encode(data).encode()).decode()

    def decode(self, text):
        return JSONBodyCodec().decode(self.fernet.decrypt(text.encode()))

IDEMPOTENCY_BODY_CODEC = "myproject.codecs.EncryptedBodyCodec"
```

## claim_idempotency_key

`claim_idempotency_key(model, claimed_by, key, fingerprint, lock_timeout=None)` returns
`(claim, created)`. It must run inside the transaction of the work the key guards. The mixin calls
it for you, and you'd call it directly only to claim keys outside DRF.

`lock_timeout` comes from the `IDEMPOTENCY_LOCK_TIMEOUT` setting (milliseconds) when the mixin calls
it. By default a retry waits as long as the first request takes. That's always correct, and the only
cost is a blocked worker while it waits. Set a timeout if your transactions can be slow: a retry
that runs out gets 409 `idempotency_key_in_flight`. The timeout applies to the claim's insert only.
`lock_timeout` is put back as soon as the insert is done, so the handler's own locks don't inherit it.
