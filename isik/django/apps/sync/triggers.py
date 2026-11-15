"""
The triggers writing the sync log: `SyncLogTrigger`, on a synced model, and `MembershipTrigger`, on a model whose
rows put people in scopes. Both build their SQL off the models they're handed - a project's own, whose tables
and columns isik can't name - and the sync log model configured when the migration is made.
"""

import pgtrigger
from django.db import connection
from django.db.models import Q

from isik.django.apps.sync import swapping
from isik.django.apps.sync.choices import Operation


def literal(value):
    """`value` as an SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


# The transaction's id, as the bigint the log's position holds.
TRANSACTION = "pg_current_xact_id()::text::bigint"


def _quote(name):
    return connection.ops.quote_name(name)


def _column(model, name):
    return _quote(model._meta.get_field(name).column)


class _LogTrigger(pgtrigger.Trigger):
    when = pgtrigger.After
    level = pgtrigger.Row
    operation = pgtrigger.Insert | pgtrigger.Update | pgtrigger.Delete

    def _insert(self, values):
        """An INSERT of one log entry, `values` {field: SQL}."""
        log = swapping.get_sync_log_model()
        values = {"xid": TRANSACTION, **values}
        columns = ", ".join(_column(log, name) for name in values)
        return f"INSERT INTO {_quote(log._meta.db_table)} ({columns}) VALUES ({', '.join(values.values())});"

    def _logged(self, body):
        # Nothing before the log's table exists - a data migration running before isik_sync's own writes no
        # entry, as the change it makes predates the feed - and the statement is planned only once it's there.
        table = swapping.get_sync_log_model()._meta.db_table
        return f"IF to_regclass({literal(_quote(table))}) IS NOT NULL THEN {body} END IF; RETURN NULL;"


class SyncLogTrigger(_LogTrigger):
    """
    An AFTER row trigger on a synced model writing one log entry for each change to a row: `upsert` for an insert
    or an update, `delete` for a delete, in the row's scope - and, for an update moving the row to another scope,
    the one it left in `old_scope`. An update changing nothing writes none.
    """

    def __init__(self, *, synced):
        self.synced = synced
        super().__init__(name="sync_log")

    def _scope(self, model, row):
        column = f"{row}.{_column(model, self.synced.scope)}"
        return f"({literal(self.synced.kind + ':')} || {column}::text)"

    def get_func(self, model):
        pk = _column(model, model._meta.pk.name)
        new, old = self._scope(model, "NEW"), self._scope(model, "OLD")
        entry = self._insert(
            {
                "op": (
                    f"CASE WHEN TG_OP = 'DELETE' THEN {literal(Operation.DELETE)} ELSE {literal(Operation.UPSERT)} END"
                ),
                "model": literal(self.synced.name),
                "object_id": f"(CASE WHEN TG_OP = 'DELETE' THEN OLD.{pk} ELSE NEW.{pk} END)::text",
                "scope": f"CASE WHEN TG_OP = 'DELETE' THEN {old} ELSE {new} END",
                "old_scope": f"CASE WHEN TG_OP = 'UPDATE' AND {old} IS DISTINCT FROM {new} THEN {old} END",
            }
        )
        # Compared as text, which every column type has - json has no equality of its own.
        unchanged = "TG_OP = 'UPDATE' AND OLD::text = NEW::text"
        return self._logged(f"IF NOT ({unchanged}) THEN {entry} END IF;")


class ParentLogTrigger(_LogTrigger):
    """
    An AFTER row trigger on the table of a relation a synced model's version covers - a many-to-many's through
    table, or a model pointing at it - writing an `upsert` of the synced row a row of it belongs to: the synced
    row's ETag moved, and the feed serves it again. An update moving a row between two synced rows writes both.
    """

    def __init__(self, *, synced, relation, owner_key):
        self.synced = synced
        self.owner_key = owner_key
        super().__init__(name=f"sync_{relation}_parent")

    def _entry(self, row):
        owner = self.synced.model
        # The column the key references - resolved: the trigger is made once every model is loaded.
        target = self.owner_key.target_field
        log = swapping.get_sync_log_model()
        columns = ", ".join(_column(log, name) for name in ("xid", "op", "model", "object_id", "scope"))
        pk, scope = _column(owner, owner._meta.pk.name), _column(owner, self.synced.scope)
        return (
            f"INSERT INTO {_quote(log._meta.db_table)} ({columns}) SELECT {TRANSACTION}, "
            f"{literal(Operation.UPSERT)}, {literal(self.synced.name)}, {pk}::text, "
            f"{literal(self.synced.kind + ':')} || {scope}::text FROM {_quote(owner._meta.db_table)} "
            f"WHERE {_quote(target.column)} = {row}.{_quote(self.owner_key.column)};"
        )

    def get_func(self, model):
        return self._logged(
            f"IF TG_OP <> 'INSERT' THEN {self._entry('OLD')} END IF; "
            f"IF TG_OP <> 'DELETE' THEN {self._entry('NEW')} END IF;"
        )


def on_row(condition, row):
    """`condition`, a `Q` over a model's fields, as a `pgtrigger.Q` over the trigger's `row` - `"old"` or `"new"`."""
    made = pgtrigger.Q()
    made.connector, made.negated = condition.connector, condition.negated
    made.children = [
        on_row(child, row) if isinstance(child, Q) else (f"{row}__{child[0]}", child[1]) for child in condition.children
    ]
    return made


class MembershipTrigger(_LogTrigger):
    """
    An AFTER row trigger on a membership model: a row coming to put its person in its scope grants it
    (`scope_granted`) - written, or updated to meet the memberships' condition - and a row ceasing to revokes it
    (`scope_revoked`) - deleted, or updated not to. An update moving a row to another person or scope does both.
    """

    def __init__(self, *, kind, memberships):
        self.kind = kind
        self.memberships = memberships
        super().__init__(name=f"sync_{kind}_membership")

    def _member(self, model, row):
        # Whether `row` puts its person in its scope - FALSE for the row an INSERT or a DELETE hasn't.
        user, scope = _column(model, self.memberships.user), _column(model, self.memberships.scope)
        member = f"{row}.{user} IS NOT NULL AND {row}.{scope} IS NOT NULL"
        if self.memberships.condition is not None:
            member = f"{member} AND ({on_row(self.memberships.condition, row.lower()).resolve(model)})"
        missing = "INSERT" if row == "OLD" else "DELETE"
        return f"(TG_OP <> {literal(missing)} AND COALESCE({member}, FALSE))"

    def _entry(self, model, operation, row):
        user, scope = _column(model, self.memberships.user), _column(model, self.memberships.scope)
        return self._insert(
            {
                "op": literal(operation),
                "scope": f"{literal(self.kind + ':')} || {row}.{scope}::text",
                "member": f"{row}.{user}::text",
            }
        )

    def get_func(self, model):
        user, scope = _column(model, self.memberships.user), _column(model, self.memberships.scope)
        was, now = self._member(model, "OLD"), self._member(model, "NEW")
        moved = (
            f"(TG_OP = 'UPDATE' AND (OLD.{user} IS DISTINCT FROM NEW.{user} "
            f"OR OLD.{scope} IS DISTINCT FROM NEW.{scope}))"
        )
        revoked = self._entry(model, Operation.SCOPE_REVOKED, "OLD")
        granted = self._entry(model, Operation.SCOPE_GRANTED, "NEW")
        return self._logged(
            f"IF {was} AND (NOT {now} OR {moved}) THEN {revoked} END IF; "
            f"IF {now} AND (NOT {was} OR {moved}) THEN {granted} END IF;"
        )
