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

## Relations the version covers

A token is one column of the row, and a many-to-many relation lives in another table: adding a user to
a group changes nothing the token sees, so a write made against the version before it passes. Name the
relations the version covers:

```python
class User(AbstractUser, BaseModel):
    concurrency_relations = ("groups", "user_permissions")
```

Each must be a many-to-many field of the model itself (not inherited from a concrete parent). Reverse
relations and foreign keys pointing at the model aren't covered.

**Computed - what naming relations does.** The ETag is computed in SQL: an MD5 of the row's token and,
for each relation, its through rows - the related object's id, and a custom through model's own fields -
in order. `updated_at` keeps meaning "this row's own fields changed": a membership change doesn't move
it.

- *Guarantees.* `PreconditionMixin` locks the row `FOR UPDATE` and computes the ETag after the lock.
  A change to a covered relation has to lock the owner row too, so it waits for the write, or the write
  waits for it, and it can't slip between the compare and the write. Adding a row already takes that
  lock, through its foreign key's check. Removing or updating one doesn't, so isik generates a trigger
  on each relation's table (`lock_<relation>_owner`, `AFTER INSERT OR UPDATE OR DELETE`) that does
  `PERFORM 1 FROM <owner> ... FOR KEY SHARE` and writes nothing. The trigger lives on a proxy model over
  the through model, `<Owner><Relation>ConcurrencyLock`, generated in the owner's app. That is pgtrigger's
  way to trigger an auto-created table, and why `makemigrations` writes the trigger into that app's
  migrations, with no operation to call by hand. Each relation locks its owner only: a user's version
  covering `groups` doesn't make a group's cover its users.
- *Same rows, same version.* Removing a row and adding it back gives the ETag it had before. That's
  intended: a write made against it loses nothing.
- *Costs.* One subquery per relation wherever the ETag is read: `retrieve`, the compare under the lock,
  the read-back after a write. A list showing `ConcurrencyTokenField` would query once per row, so
  annotate its queryset with `with_concurrency_etags(queryset)`:

  ```python
  def get_queryset(self):
      return with_concurrency_etags(User.objects.all())
  ```

  Bulk membership changes lock the owners they touch, for as long as their transaction runs.
- *Checks.* `concurrency_token_problems()` (`isik.E002`) refuses a name that isn't a many-to-many field
  of the model. The database check **`isik.W002`** (run by `migrate`, and by `check --database default`)
  warns while a relation's trigger isn't installed, or is outdated. It's a warning because `migrate`
  runs it before applying the migration that installs the trigger.

`concurrency_etag_expression(model)` is the SQL itself, for an annotation of your own.

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

A model whose version covers relations takes one object per relation, as `obj` or as
`(obj, through_defaults)`. Each object is added, removed, added again and cleared, and every step must
move the ETag from the one before it:

```python
assert_concurrency_field_changes(user, related={"groups": group, "user_permissions": permission})
```

## concurrency_etag(instance), current_concurrency_etag(model, pk), has_concurrency_token(instance)

The version as a strong ETag, quotes included - `'"1792585815123456"'`. A model covering relations has
an MD5 instead, read from the annotation `with_concurrency_etags()` made, or else from the database. A datetime is its whole
microseconds since the epoch: a timestamptz's full resolution, which a rendered datetime (a project's
`DATETIME_FORMAT`, a JavaScript `Date`) may not keep. A UUID is its hex, an integer its digits.
Clients treat it as opaque and send back what they got. `current_concurrency_etag()` reads it from the
database writes are routed to, and gives `None` when the row is gone. `has_concurrency_token()` says
whether the instance's model has a token at all. Each takes a `SimpleLazyObject` - `request.user` - as
the object it wraps.
