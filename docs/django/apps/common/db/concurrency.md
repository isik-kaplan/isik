# concurrency

`isik.django.apps.common.db.concurrency` - a model's **concurrency token**: the field its version is
read from. A write made against an older version can then be told apart from one made against the
current one, and refused. [`PreconditionMixin`](../../../drf/viewsets/preconditions.md) sends the
token as an `ETag` and compares `If-Match` with it.

## Which field

A model's token is its `concurrency_field`. By default it's:

- `version` on a [`VersionedModel`](models.md#versionedmodel), whose trigger sets it to
  `OLD.version + 1` on every UPDATE;
- else `updated_at` on a `DatabaseTimestampsModel` (so every `BaseModel`), whose trigger moves it
  strictly forward on every UPDATE.

Both triggers fire whatever wrote the row - `save()`, `QuerySet.update()`, `bulk_update()`, raw SQL -
and whatever the write set the column to. That is what makes them tokens. A model that is both uses
`version`.

Any other field is declared, with the reason it changes on every write. A trigger of the project's
own, for instance: its SQL can't be read for that, so isik takes the reason instead. The reason is an
`UnprovenConcurrencyToken` exemption (rule `isik.concurrency-token.unproven`), held to its 40-character
floor and listed by `manage.py exemptions`:

```python
from isik.django.apps.common.db.concurrency import ConcurrencyToken


class Invoice(models.Model):
    revision = models.BigIntegerField(default=1)
    concurrency_field = ConcurrencyToken("revision", reason="bumped by the ledger's own trigger on every write")
```

Naming `updated_at` or `version` as a plain string (`concurrency_field = "updated_at"`) is enough
when isik's trigger keeps it.

## concurrency_token_problems(model)

Why `model`'s token can't be trusted, one sentence each, or `[]`. The token must exist, be a concrete
column that can't be null, be an integer, a datetime or a UUID (each has a text form that is a valid
ETag), and be kept by one of isik's triggers or declared with `ConcurrencyToken`. The system check
`isik.E002` runs it for every model a routed `PreconditionMixin` view writes.

## assert_concurrency_field_changes(instance)

Proves on a real database that the token changes on every write, for a test:

```python
def test_the_invoice_revision_is_a_concurrency_token(invoice):
    assert_concurrency_field_changes(invoice)
```

It saves `instance`, then twice writes the token back to the value it already holds with
`QuerySet.update()`, back to back. It raises `AssertionError` naming the first write that left a token
an earlier one had already left.

## concurrency_etag(instance), current_concurrency_etag(model, pk)

The token as a strong ETag, quotes included - `'"1792585815123456"'`. A datetime is its whole
microseconds since the epoch: a timestamptz's full resolution, which a rendered datetime (a project's
`DATETIME_FORMAT`, a JavaScript `Date`) may not keep. A UUID is its hex, an integer its digits.
Clients treat it as opaque and send back what they got. `current_concurrency_etag()` reads it from the
database, and gives `None` when the row is gone.
