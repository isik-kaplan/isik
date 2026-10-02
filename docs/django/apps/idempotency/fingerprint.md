# fingerprint

`isik.django.apps.idempotency.fingerprint` - what makes a retry the same request as the first one.

## request_fingerprint

`request_fingerprint(request, normalize=None)` is a sha256 over the method, path, query parameters
(sorted by name, each name's values kept in order) and `normalize(request)`. A key spent on one
fingerprint and sent with another gets 422.

## canonical_body - the default normalizer

The request's parsed body (`request.data`), written as JSON with **sorted keys**. A client that
reorders its JSON on retry isn't told its payload changed. A form or multipart body becomes
`{field: [every value, in order]}`.

An uploaded file is described by its name, size and the sha256 of its content, rather than by the
raw multipart bytes. The bytes would differ on every retry, because each one is sent with a new
boundary. The file is rewound after hashing, so the handler still reads it from the start.

Anything else JSON can't write raises `TypeError` and asks for a normalizer.

## Custom normalizers

A normalizer is a function of the request that returns anything `canonical_json()` can write. Set
one:

- **globally** with `IDEMPOTENCY_NORMALIZE`, a callable or a dotted path to one;
- **per view** with `idempotency_normalize = fn` on the class;
- **per action** with `@action(..., idempotency_normalize=fn)`.

The most specific one wins. A normalizer that only drops a field can build on the default:

```python
from isik.django.apps.idempotency.fingerprint import canonical_body

def without_nonce(request):
    return {key: value for key, value in canonical_body(request).items() if key != "nonce"}
```

## canonical_json

`canonical_json(value)` is the JSON the fingerprint is taken over: sorted keys at every depth, no
extra whitespace, and uploads described as above.
