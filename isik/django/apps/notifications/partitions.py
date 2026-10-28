"""
Partitions - the parts a project's data lives in, such as a schema per tenant. isik knows them only by
the project's `PARTITIONS`, `IN_PARTITION` and `PARTITION_EXISTS` hooks; with none set there's one,
keyed None, and entering it does nothing at all.

A notice's trigger stores the partition its row belongs to, read by a resolver - the kind's own
`partition=`, else `DEFAULT_PARTITION`:

- `Constant("public")` - always the one partition, for a model that only ever lives in it.
- `Connection()` - the partition the connection is in, `current_schema()`. Right when the row lives in it.
- `Column("schema_name")` - a column of the row naming its partition.
- `Follow("organization", "schema_name")` - a key of the row, to a row whose column names it.
- `Expression(builder)` - `builder(model)`, returning SQL over NEW and OLD, for anything else.

Every table and column name is read off the models, so the SQL lands in the migration and follows a
rename there. A resolver's `check(model, kind)` says what's wrong with it, for `isik.E014`.

The ledger has to live in exactly one place every partition reaches: `VariantGuard` and each notice's
trigger name the ledger's tables unqualified, so a ledger copied into every partition would have each
one writing, and checking against, its own copy.
"""

from contextlib import contextmanager

from django.core.exceptions import FieldDoesNotExist
from django.db import connection, models, transaction

from isik._internal.translation import gettext as _
from isik.django.apps.notifications import conf


class PartitionGone(Exception):
    """The partition a notice or delivery is for no longer exists - so nothing in it can be reached."""

    def __init__(self, key):
        self.key = key
        super().__init__(_("The partition %(key)r no longer exists.") % {"key": key})


def _listed(key):
    return key in set(conf.setting("PARTITIONS")())


@contextmanager
def in_partition(key):
    """
    Enters partition `key` - `IN_PARTITION(key)`, inside a transaction that first asks `PARTITION_EXISTS`
    (by default, whether `PARTITIONS()` lists it) and raises `PartitionGone` when it doesn't.

    isik checks that the partition existed when the work began. Whether it can still vanish before the
    transaction ends depends on what entering one does in the project - a schema per tenant, say, is no
    error to enter after it's dropped: Postgres reads the next schema on the search path instead. So an
    `IN_PARTITION` that has to be sure pins the partition itself (a schema, by reading one of its tables
    by qualified name), and the check here is the one shards and deliveries keep their books by. With no
    partitions, it does nothing - not even ask.
    """
    if not conf.partitioned():
        yield
        return
    exists = conf.setting("PARTITION_EXISTS") or _listed
    with transaction.atomic():
        if key is None or not exists(key):
            raise PartitionGone(key)
        with conf.setting("IN_PARTITION")(key):
            yield


def _quote(name):
    return connection.ops.quote_name(name)


def _row(column):
    """A column of the row a trigger fired for - OLD's for a delete, NEW's otherwise."""
    return f"CASE WHEN TG_OP = 'DELETE' THEN OLD.{column} ELSE NEW.{column} END"


def _concrete(model, name):
    """`model`'s field `name` when it has a column of its own, else None."""
    try:
        field = model._meta.get_field(name)
    except FieldDoesNotExist:
        return None
    return field if getattr(field, "column", None) else None


class PartitionSource:
    """The base of a partition resolver: `resolve(model)` is its SQL, `check(model, kind)` its problems."""

    def resolve(self, model):
        raise NotImplementedError

    def check(self, model, kind):
        return []


class Constant(PartitionSource):
    """Always partition `key` - for a model that only ever lives in that one."""

    def __init__(self, key):
        self.key = key

    def resolve(self, model):
        return "'" + str(self.key).replace("'", "''") + "'"

    def __repr__(self):
        return f"Constant({self.key!r})"


class Connection(PartitionSource):
    """The partition the connection is in - right when the row being written lives in it."""

    def resolve(self, model):
        return "current_schema()"

    def __repr__(self):
        return "Connection()"


class Column(PartitionSource):
    """A column of the row naming the partition it belongs to."""

    def __init__(self, field):
        self.field = field

    def resolve(self, model):
        return _row(_quote(model._meta.get_field(self.field).column))

    def check(self, model, kind):
        if _concrete(model, self.field) is None:
            return [
                _("%(kind)s reads its partition from %(field)s, which %(model)s has no column for.")
                % {"kind": kind.name, "field": self.field, "model": model._meta.label}
            ]
        return []

    def __repr__(self):
        return f"Column({self.field!r})"


class Follow(PartitionSource):
    """
    A key of the row, to a row whose column names the partition: `Follow("organization", "schema_name")`.
    A key that may be empty leaves its notices with no partition, which a partitioned project can't
    deliver - so it's refused unless `allow_null=True` says those are meant to go nowhere.
    """

    def __init__(self, key, field, *, allow_null=False):
        self.key, self.field, self.allow_null = key, field, allow_null

    def resolve(self, model):
        key = model._meta.get_field(self.key)
        target = key.related_model
        return (
            f"(SELECT {_quote(target._meta.get_field(self.field).column)} FROM {_quote(target._meta.db_table)} "
            f"WHERE {_quote(key.target_field.column)} = {_row(_quote(key.column))})"
        )

    def check(self, model, kind):
        key = _concrete(model, self.key)
        if key is None or not key.is_relation:
            return [
                _("%(kind)s follows %(key)s to its partition, which isn't a key of %(model)s.")
                % {"kind": kind.name, "key": self.key, "model": model._meta.label}
            ]
        problems = []
        if _concrete(key.related_model, self.field) is None:
            problems.append(
                _("%(kind)s reads its partition from %(model)s.%(field)s, which has no column.")
                % {"kind": kind.name, "model": key.related_model._meta.label, "field": self.field}
            )
        if key.null and not self.allow_null:
            problems.append(
                _(
                    "%(kind)s follows %(key)s to its partition, which may be empty - a notice with no partition "
                    "reaches nobody. Say allow_null=True if that's meant."
                )
                % {"kind": kind.name, "key": self.key}
            )
        # Postgres deletes the row a database-level cascade comes from before its dependents, whose
        # triggers then find nothing to follow. Django's own CASCADE deletes the dependents first.
        database_cascade = getattr(models, "DB_CASCADE", None)
        if database_cascade is not None and key.remote_field.on_delete is database_cascade and "DELETE" in str(kind.on):
            problems.append(
                _(
                    "%(kind)s follows %(key)s to its partition on a delete, and the database deletes what it "
                    "points at first - use Column, or Django's CASCADE."
                )
                % {"kind": kind.name, "key": self.key}
            )
        return problems

    def __repr__(self):
        extra = ", allow_null=True" if self.allow_null else ""
        return f"Follow({self.key!r}, {self.field!r}{extra})"


class Expression(PartitionSource):
    """`builder(model)`'s SQL, over NEW and OLD - for a partition no other resolver reaches."""

    def __init__(self, builder):
        self.builder = builder

    def resolve(self, model):
        return self.builder(model)

    def __repr__(self):
        return f"Expression({getattr(self.builder, '__qualname__', self.builder)!r})"
