"""
The triggers the notifications apps compile: `NoticeTrigger`, writing a kind's notices on its model, and
`VariantGuard`, holding a kind-holder's variant to what its kind declares. Both build their SQL off the
model they're handed - a consumer's own concrete model, whose table and columns isik can't name.
"""

import pgtrigger
from django.apps import apps
from django.db import connection

from isik.django.apps.notifications import conf, swapping


def literal(value):
    """`value` as an SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


# The history context's metadata, which pghistory sets before each statement - NULL outside one.
METADATA = "NULLIF(current_setting('pghistory.context_metadata', true), '')::jsonb"


def actor():
    """The SQL reading who acted: `ACTOR_CONTEXT_KEY` of the history context, as text - NULL with no key."""
    key = conf.setting("ACTOR_CONTEXT_KEY")
    if key is None:
        return "NULL"
    return f"({METADATA} ->> {literal(key)})"


def context():
    """The SQL reading `NOTICE_CONTEXT_KEYS` of the history context into a jsonb object - those it holds."""
    keys = conf.setting("NOTICE_CONTEXT_KEYS")
    if not keys:
        return "'{}'::jsonb"
    pairs = ", ".join(f"{literal(key)}, {METADATA} -> {literal(key)}" for key in keys)
    return f"jsonb_strip_nulls(jsonb_build_object({pairs}))"


def _quote(name):
    return connection.ops.quote_name(name)


def _column(model, name):
    return _quote(model._meta.get_field(name).column)


class NoticeTrigger(pgtrigger.Trigger):
    """
    An AFTER row trigger writing `kind`'s notices - one for each of its variants whose condition holds, or
    one with no variant for a kind declaring none - unless the kind is retired. The subject is the row:
    NEW, or OLD for a delete.
    """

    when = pgtrigger.After
    level = pgtrigger.Row

    def __init__(self, *, kind):
        self.kind = kind
        super().__init__(name=kind.trigger_name, operation=kind.on, condition=kind.condition)

    def _value(self, model, name, row=None):
        # The subject's column - OLD's for a delete, NEW's otherwise - unless `row` says which.
        column = _column(model, name)
        if row:
            return f"{row}.{column}"
        return f"CASE WHEN TG_OP = 'DELETE' THEN OLD.{column} ELSE NEW.{column} END"

    def _object(self, model, row=None):
        pairs = ", ".join(f"{literal(name)}, {self._value(model, name, row)}" for name in self.kind.snapshot)
        return f"jsonb_build_object({pairs})"

    def _partition(self, model):
        # The kind's own resolver, else DEFAULT_PARTITION's - read now, so its SQL lands in the migration.
        source = self.kind.partition or conf.setting("DEFAULT_PARTITION")
        return f"({source.resolve(model)})::text" if source is not None else "NULL"

    def _insert(self, model, variant):
        Notice, NoticeKindPolicy = swapping.get_notice_model(), swapping.get_kind_policy_model()
        notice, policy = Notice._meta, NoticeKindPolicy._meta
        columns = ", ".join(
            _column(Notice, name)
            for name in (
                "kind",
                "variant",
                "subject_label",
                "subject_id",
                "subject_partition",
                "snapshot",
                "previous",
                "actor_id",
                "context",
                "pgh_context",
            )
        )
        previous = f"CASE WHEN TG_OP = 'UPDATE' THEN {self._object(model, 'OLD')} END" if self.kind.previous else "NULL"
        values = ", ".join(
            [
                literal(self.kind.name),
                literal(variant) if variant is not None else "NULL",
                literal(model._meta.label),
                f"({self._value(model, model._meta.pk.name)})::text",
                self._partition(model),
                self._object(model),
                previous,
                actor(),
                context(),
                "_pgh_attach_context()",
            ]
        )
        active = (
            f"SELECT 1 FROM {_quote(policy.db_table)} WHERE {_column(NoticeKindPolicy, 'kind')} = "
            f"{literal(self.kind.name)} AND {_column(NoticeKindPolicy, 'retired_at')} IS NULL"
        )
        return f"INSERT INTO {_quote(notice.db_table)} ({columns}) SELECT {values} WHERE EXISTS ({active});"

    def get_func(self, model):
        """
        Writes a notice only for a kind whose policy row is there and not retired - none during `migrate`,
        then, before the ledger's tables exist or the mirror has written the kind's row: a data migration
        isn't something that happened. The tables are looked for first, and their statements are planned
        only once they're there, so a migration writing a watched row needs no dependency on the ledger.
        """
        if not self.kind.variants:
            body = self._insert(model, None)
        else:
            body = " ".join(
                f"IF {condition.resolve(model) if condition is not None else 'TRUE'} THEN "
                f"{self._insert(model, variant)} END IF;"
                for variant, condition in self.kind.variants.items()
            )
        policy = swapping.get_kind_policy_model()._meta.db_table
        return f"IF to_regclass({literal(_quote(policy))}) IS NOT NULL THEN {body} END IF; RETURN NULL;"


class VariantGuard(pgtrigger.Trigger):
    """
    A BEFORE INSERT OR UPDATE trigger on a kind-holder - a subscription, an endpoint kind, an inbox item -
    refusing a kind that doesn't exist, and a variant its kind doesn't declare. With an `owner`, it also
    refuses a row naming no variant beside one naming some, for the same owner and kind, and the reverse:
    no variant already means every one. It takes an advisory lock on the (owner, kind) pair first, so two
    writers can't both pass.
    """

    when = pgtrigger.Before
    operation = pgtrigger.Insert | pgtrigger.Update

    def __init__(self, *, name, owner=None):
        self.owner = owner
        super().__init__(name=name)

    def _refuse(self, message):
        return f"RAISE EXCEPTION {literal(message)} USING ERRCODE = 'check_violation';"

    def _declared(self, model):
        # A model swapped out - isik's own, in a project owning it - never has a table, so its guard stays as
        # isik shipped it, naming isik's policy table, and the shipped migration stays the one it matches.
        NoticeKindPolicy = (
            apps.get_model(swapping.DEFAULTS[swapping.KIND_POLICY_MODEL])
            if model._meta.swapped
            else swapping.get_kind_policy_model()
        )
        variant, kind = _column(model, "variant"), _column(model, "kind")
        # Every row, not only one naming a variant: a kind key without a database constraint - one
        # reaching across schemas - would otherwise take a kind that doesn't exist.
        policy = _quote(NoticeKindPolicy._meta.db_table)
        lookup = f"SELECT 1 FROM {policy} WHERE {_column(NoticeKindPolicy, 'kind')} = NEW.{kind}"
        return (
            f"IF NOT EXISTS ({lookup}) THEN {self._refuse(f'{model._meta.label}: no kind is called that.')} END IF; "
            f"IF NEW.{variant} IS NOT NULL AND NOT EXISTS ({lookup} AND "
            f"{_column(NoticeKindPolicy, 'variants')} @> jsonb_build_array(NEW.{variant})) THEN "
            f"{self._refuse(f'{model._meta.label}: the kind declares no such variant.')} END IF;"
        )

    def _exclusive(self, model):
        variant, kind, owner = _column(model, "variant"), _column(model, "kind"), _column(model, self.owner)
        pk = _column(model, model._meta.pk.name)
        table = _quote(model._meta.db_table)
        refusal = self._refuse(
            f"{model._meta.label}: a row naming no variant, which covers every one, "
            f"cannot sit beside one naming a variant for the same {self.owner} and kind."
        )
        return (
            f"PERFORM pg_advisory_xact_lock(hashtext({literal(model._meta.db_table)}), "
            f"hashtext(NEW.{owner}::text || ':' || NEW.{kind}::text)); "
            f"IF EXISTS (SELECT 1 FROM {table} WHERE {owner} = NEW.{owner} AND {kind} = NEW.{kind} "
            f"AND {pk} IS DISTINCT FROM NEW.{pk} AND ({variant} IS NULL) <> (NEW.{variant} IS NULL)) THEN "
            f"{refusal} END IF;"
        )

    def get_func(self, model):
        parts = [self._declared(model)]
        if self.owner:
            parts.append(self._exclusive(model))
        return " ".join([*parts, "RETURN NEW;"])
