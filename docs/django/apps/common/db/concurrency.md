# concurrency

`isik.django.apps.common.db.concurrency` - a model's **concurrency token**: the field its version is
read from. A write made against an older version can then be told apart from one made against the
current one, and refused. [`PreconditionMixin`](../../../drf/viewsets/preconditions.md) sends the
token as an `ETag` and compares `If-Match` with it.

## Which field

A model's token is its `concurrency_field`. By default it's:

- `version` on a [`VersionedModel`](models.md#versionedmodel), whose trigger sets it to
  `OLD.version + 1` whenever the row changes;
- else `updated_at` on a `DatabaseTimestampsModel` (so every `BaseModel`), whose trigger moves it
  strictly forward whenever the row changes.

Both triggers fire whatever wrote the row - `save()`, `QuerySet.update()`, `bulk_update()`, raw SQL -
and whatever the write set the column to: a write can't choose the token, or take it back. That is
what makes them tokens. A write that changes nothing leaves them where they were - there's nothing a
write made against the version before it could lose
([a write that changes nothing](models.md#a-write-that-changes-nothing)). A model that is both uses
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

Each is one of:

- a **many-to-many** field of the model itself (not inherited from a concrete parent) - its rows are the
  through table's;
- the accessor of a **foreign key or one-to-one pointing at the model** - `discount_set`, or the
  `related_name` it gives (`discounts`), `storeprofile` for a one-to-one - its rows are the pointing
  model's own: every field of each, so editing one moves the version as adding or deleting one does.

```python
class Store(BaseModel):
    concurrency_relations = ("discounts", "profile")


class Discount(models.Model):
    store = models.ForeignKey(Store, models.CASCADE, related_name="discounts")


class StoreProfile(models.Model):
    store = models.OneToOneField(Store, models.CASCADE, related_name="profile")
```

A reverse many-to-many - the other side of someone else's - isn't covered: name it on the model that
declares it.

`concurrency_strategy` picks how the version covers them - three strategies, below. All three keep a
model that names no relations exactly as it was, ETag format included.

| | Computed (default) | Column | Parts |
|---|---|---|---|
| Declared | `concurrency_relations` | `+ concurrency_strategy = "column"` | `concurrency_strategy = ConcurrencyParts(reason=...)` |
| ETag | MD5 computed in SQL | the token, as ever | SHA-256 of the token and `concurrency_parts()` |
| `updated_at` on a relation change | stays | moves | stays |
| Covers | every named relation's rows | every named relation's rows | whatever `concurrency_parts()` reads |
| Read cost | a subquery per relation | none | the method's own queries |

### Computed

The default once `concurrency_relations` names anything. The ETag is computed in SQL: an MD5 of the
row's token and, for each relation, its rows - a through row's related object and a custom through
model's own fields, or every field of a row pointing at the owner - in order. `updated_at` keeps meaning "this row's own fields changed".

- *When.* A version that must see memberships, with `updated_at` left to the row's own fields - the
  usual choice.
- *Guarantees.* `PreconditionMixin` locks the row `FOR UPDATE` and computes the ETag after the lock.
  A change to a covered relation has to lock the owner row too, so it waits for the write, or the write
  waits for it, and it can't slip between the compare and the write. Adding a row already takes that
  lock, through its foreign key's check. Removing or updating one doesn't, so isik generates a trigger
  on each relation's table (`lock_<relation>_owner`, `AFTER INSERT OR UPDATE OR DELETE`) that does
  `PERFORM 1 FROM <owner> ... FOR KEY SHARE` and writes nothing. Each relation locks its owner only: a
  user's version covering `groups` doesn't make a group's cover its users. The same rows always give the
  same ETag - one removed and added back changes nothing a write made against it could lose.
- *Costs.* One subquery per relation wherever the ETag is read: `retrieve`, the compare under the lock,
  the read-back after a write. A list showing `ConcurrencyTokenField` would query once per row, so
  annotate its queryset with `with_concurrency_etags(queryset)`. Bulk membership changes lock the owners
  they touch, for as long as their transaction runs.

```python
class User(AbstractUser, BaseModel):
    concurrency_relations = ("groups", "user_permissions")

    # a viewset listing users with their ETags
    def get_queryset(self):
        return with_concurrency_etags(User.objects.all())
```

### Column

The relations move the token itself. Each relation's table gets a trigger (`bump_<relation>_owner`)
that runs an `UPDATE` of the owner row moving its first database-kept field one step forward, as
[`touch_rows()`](models.md#a-write-that-changes-nothing) does - a write naming a kept field forward is a
change, so the owner's own BEFORE UPDATE triggers move every kept field. An owner keeping neither gets
an `UPDATE` setting nothing that changes, for its own token's trigger to fire on. Those triggers only
assign `NEW` and never touch the relation's table, so it's one `UPDATE` of the owner per row changed
there - never a loop.

- *When.* The cheapest reads: the ETag is the token, in today's format, with no subquery and nothing to
  annotate.
- *Guarantees.* The `UPDATE` locks the owner row, so the race is closed as in Computed. Every change is
  a new version - the same rows removed and added back are two of them.
- *Costs.* `updated_at` moves on a relation change too, since the owner row is updated: use Computed
  for `updated_at` to mean the row's own fields only. A bulk change writes one `UPDATE` of the owner per
  row it changes, and holds the owner's row lock until it commits. Needs a token a trigger moves:
  `version` on a `VersionedModel`, `updated_at`, or a `ConcurrencyToken` whose trigger fires on any
  `UPDATE`.

```python
class Squad(VersionedModel, BaseModel):
    members = models.ManyToManyField(User)
    concurrency_relations = ("members",)
    concurrency_strategy = "column"
```

### Parts

The model says what its version covers, in Python: `concurrency_parts(self)` returns a list of JSON-able
values (a datetime, a UUID or a `Decimal` reads as its text), and the ETag is a SHA-256 of the token
and that list. The reason is a `ConcurrencyParts` exemption (rule `isik.concurrency-parts.unproven`), held
to its floor and listed by `manage.py exemptions`.

- *When.* A version that depends on something the other two can't read - a reverse relation, a value in
  another table, a computed rule.
- *What it can't guarantee.* Nothing proves `concurrency_parts()` returns everything a write could lose:
  a part it leaves out is a change a stale write gets through. What it reads isn't locked by the row lock
  either, so a change can land between the compare and the write. Name the many-to-many relations it
  reads in `concurrency_relations` to give them the lock trigger. Anything else it reads has no such
  protection unless the project adds a trigger of its own.
- *Costs.* Whatever queries the method runs, wherever the ETag is read, once per row in a list -
  `with_concurrency_etags()` can't annotate it.

```python
class Roster(BaseModel):
    tags = models.ManyToManyField(Tag)
    concurrency_relations = ("tags",)  # locked, as the parts read them
    concurrency_strategy = ConcurrencyParts(reason="its tags are read by concurrency_parts(), and nothing else counts")

    def concurrency_parts(self):
        return sorted(self.tags.values_list("label", flat=True))
```

### Shared by all three

- The lock or bump trigger lives on a proxy model over the model whose table holds the relation's rows -
  the through model, or the model pointing at the owner - `<Owner><Relation>ConcurrencyLock`, generated in
  the owner's app. That is pgtrigger's way to trigger an auto-created table, or a model of another app,
  and why `makemigrations` writes the trigger into the owner app's migrations, with no operation to call
  by hand.
- `concurrency_token_problems()` (`isik.E002`) refuses a name that is neither a many-to-many field of
  the model nor a foreign key or one-to-one pointing at it, a strategy it doesn't know, Computed or Column naming no relations, and `ConcurrencyParts`
  without a `concurrency_parts()` method. The database check **`isik.W002`** (run by `migrate`, and by
  `check --database default`) warns while a relation's trigger isn't installed, or is outdated. It's a
  warning because `migrate` runs it before applying the migration that installs the trigger.
- `concurrency_strategy(model)` says which applies: `"computed"`, `"column"`, `"parts"`, or None.
  `concurrency_etag_expression(model)` is Computed's SQL, for an annotation of your own.

## concurrency_token_problems(model)

Why `model`'s token can't be trusted, one sentence each, or `[]`. The token must exist, be a concrete
column that can't be null, be an integer, a datetime or a UUID (each has a text form that is a valid
ETag), and be kept by one of isik's triggers or declared with `ConcurrencyToken`. The system check
`isik.E002` runs it for every model a routed `PreconditionMixin` view writes.

## assert_concurrency_field_changes(instance)

Proves on a real database that a write can't choose the token, for a test:

```python
def test_the_invoice_revision_is_a_concurrency_token(invoice):
    assert_concurrency_field_changes(invoice)
```

With `QuerySet.update()`, back to back, it writes an older token, which must not take, then twice a
newer one, each of which must move the token to a value it hasn't held and that isn't the one the write
named. It raises `AssertionError` naming the first write that didn't. A token nothing keeps fails at the
older one: it takes whatever a write names.

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
