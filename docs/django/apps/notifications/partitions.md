# Partitions

A partition is a part your data is split into - a schema per tenant, a database per region. A project
that doesn't split its data sets none of this: there's one partition, keyed `None`, entering it does
nothing, and no partition hook is ever asked anything.

| setting | |
|---|---|
| `PARTITIONS` | `f()` -> every partition's key (text) |
| `IN_PARTITION` | `f(key)` -> a context manager entering one |
| `PARTITION_EXISTS` | `f(key)` -> whether it still exists; by default, whether `PARTITIONS()` lists it |
| `DEFAULT_PARTITION` | where a kind with no `partition=` reads its row's partition from |

With django-tenants, for instance:

```python
from isik.django.apps.notifications.partitions import Connection

ISIK_NOTIFICATIONS = {
    "PARTITIONS": "acme.tenancy.schema_names",               # the schemas holding people to notify
    "IN_PARTITION": "django_tenants.utils.schema_context",
    "PARTITION_EXISTS": "django_tenants.utils.schema_exists",
    "DEFAULT_PARTITION": Connection(),                       # only if rows live where the connection is
}
```

The list is the project's own - only it knows which schemas hold people to notify - and whether one
still exists comes from the database. Install the ledger app once (in the shared schema) and the audience
app in every partition.

## Which partition a notice belongs to

**A notice's partition is where its people are, not where its row is** - it's the partition fan-out enters to
find the audience. For a row in a tenant's own schema those are the same, which is what makes the mistake
easy: a row in the shared schema naming a tenant, whose audience is that tenant's people, belongs to the
tenant's partition - `Column` or `Follow` - and `Constant("public")` there would ask the shared schema's
people instead, silently. `Constant` is for a kind whose audience really is in one fixed partition, or a
cross-partition kind, whose shards come from its audience's `partitions(notice)`.

A notice's trigger stores that partition, read by a resolver - the kind's own
`partition=`, else `DEFAULT_PARTITION`. Every table and column name is read off the models, so the SQL
lands in the migration and a rename shows there as migration drift, never as a string nothing reads:

| resolver | the partition is |
|---|---|
| `Constant("public")` | always that one - for a model that only ever lives in it |
| `Connection()` | the one the connection is in, `current_schema()` - right only when the row lives in it |
| `Column("schema_name")` | a column of the row |
| `Follow("organization", "schema_name")` | a column of the row a key of the row points at |
| `Expression(builder)` | `builder(model)` - SQL over `NEW` and `OLD`, built from `_meta` in your own module |

```python
@notifies("membership_removed", on=pgtrigger.Delete, ..., partition=Follow("organization", "schema_name"))
class Membership(models.Model): ...
```

`Connection()` is wrong for a row in a shared table - a membership in the public schema, written while a
tenant's schema is first on the search path, belongs to the partition **it names**, not the one the
request came through. That's `Follow`'s case.

`isik.E014` holds each resolver to the model: a column the row hasn't, a "key" that isn't one, a target
without the column. Two more:

- `Follow` through a key that may be empty is refused unless `allow_null=True` says those notices are
  meant to reach nobody - in a partitioned project, fan-out drops a notice with no partition, logging it.
- `Follow` on a `Delete` kind through a key whose `on_delete` is a **database** cascade (`models.DB_CASCADE`,
  where your Django has it) is refused: Postgres deletes the row it points at first, so the trigger finds
  nothing to follow. Django's own `CASCADE` deletes the dependents first and is fine. A cascade written by
  hand into the database can't be seen from Django - don't put one under a `Follow`.

With partitions configured, every trigger-sourced kind needs a resolver - its own, or the default - or
`isik.E014` says so.

## Single and cross-partition kinds

A kind's `scope`:

- **`Scope.SINGLE_PARTITION`** (the default) - its audience lives in one partition. Fan-out enters it,
  resolves the recipients, and writes the deliveries, in the same transaction that stamps the notice.
- **`Scope.CROSS_PARTITION`** - its audience spans partitions (`spans_partitions = True`, with
  `partitions(notice)` naming them). Fan-out writes one `FanOutShard` per partition instead, and a task
  per shard writes that partition's deliveries. Each shard is claimed, retried and abandoned like a
  delivery, and "which partitions weren't reached" is a query over the shards.

A notice that fails to fan out is set aside as a shard too - with its own backoff, attempts and
abandonment - so it never heads the queue again and holds up everyone else's.

## A partition that's gone

Partitions get dropped - a tenant deleted, its schema with it - and a list of them can be stale by the
time it's read. Every entry into a partition goes through `in_partition()`, which opens a transaction,
asks `PARTITION_EXISTS` inside it, and raises `PartitionGone` when the answer is no. A shard, delivery or
manual retry into a partition that's gone is abandoned at once, with that reason, rather than retried for
hours.

That check matters more than it looks: entering a schema that no longer exists is **no error** to
Postgres, which reads the next schema on the search path instead - for django-tenants, `public`. Without
it, a dead tenant's notices would be read against the shared schema's people.

isik checks that the partition existed when the work began. Whether it can still vanish before that
transaction ends depends on what your `IN_PARTITION` does:

- The default `PARTITION_EXISTS` reads `PARTITIONS()`, which is right only when the code listing your
  partitions is the code creating and dropping them. If the list and the real partitions can disagree -
  a schema dropped while its row still lists it - supply a direct test, as `schema_exists` above is.
- A catalog check like `schema_exists` takes no lock, so a schema can still be dropped between it and the
  work. An `IN_PARTITION` that has to be sure pins the partition itself - for a schema, by reading one of
  its tables by **qualified** name (`SELECT 1 FROM "tenant"."some_table" LIMIT 0`), which raises when the
  schema is gone and holds a lock a `DROP SCHEMA` waits on. It only holds inside the transaction the work
  runs in, which `in_partition()` opens: pin inside `IN_PARTITION`, not in a transaction of your own
  around part of the work.

## One ledger

The ledger has to live in exactly one place every partition can reach. A notice's trigger and the
audience's `VariantGuard` name the ledger's tables unqualified, so they find the one copy through the
search path. A ledger installed in every partition would have each partition writing, and checking
against, its own copy - silently.
