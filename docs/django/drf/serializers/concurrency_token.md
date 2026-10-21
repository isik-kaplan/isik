# concurrency_token

`ConcurrencyTokenField` - an object's [concurrency token](../../apps/common/db/concurrency.md) as
[`PreconditionMixin`](../viewsets/preconditions.md) sends it in `ETag`: the same string, quotes and
all. A list has no `ETag` per item, so each item carries the one to send back in `If-Match`:

```python
from isik.django.drf.serializers import ConcurrencyTokenField


class WidgetSerializer(serializers.ModelSerializer):
    etag = ConcurrencyTokenField()

    class Meta:
        model = Widget
        fields = ["id", "name", "etag"]
```

```json
[{"id": "…", "name": "bolt", "etag": "\"1792585815123456\""}]
```

Always read-only, whatever is passed, and always the whole object as its source - a value a request
sends for it is ignored. Documented as a string.

The value in a write's response is the one the write left: `PreconditionMixin.perform_update()` reads
the row back from the database before the body is rendered - the token, and anything else a trigger
set, such as `updated_at` beside a `version`. A `perform_update()` of your own that
doesn't call `super()` leaves the body showing the token the row was read with; the `ETag` header is
read back either way.
