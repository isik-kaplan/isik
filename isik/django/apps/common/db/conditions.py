"""
Trigger conditions that leave a model's database-kept fields out - `version` on a `VersionedModel`,
`updated_at` on a `DatabaseTimestampsModel` - and `kept_field_condition_problems()`, the system check
`isik.E013` over the conditions that don't.

isik's own triggers move those fields whenever a write changes the row. A condition comparing them
therefore sees a change to any column as a change to them: `AnyChange(exclude=["name"])` still fires
for a write to `name` alone, its `updated_at` having moved with it. A BEFORE trigger whose name sorts
before isik's (`bump_version`, `stamp_updated_at`) sees them before they're kept instead - a stale
value a write sent back reads as a change there. These are pgtrigger's (and pghistory's) four change
conditions, with the kept fields left out of whatever they compare:

    from isik.django.apps.common.db.conditions import AnyChange

    pghistory.UpdateEvent(condition=AnyChange(exclude=["last_seen"]))
"""

import copy

import pgtrigger
from django.apps import apps
from django.db import models
from django.db.models import F, Q

from isik._internal.translation import gettext as _
from isik.django.apps.common.db.models import database_kept_fields


def _kept_fields_of(model):
    """
    `model`'s database-kept fields. A migration resolves a condition against a model of its own
    state, which is no subclass of the bases that say which they are - so its registered model says.
    """
    try:
        registered = apps.get_model(model._meta.label)
    except LookupError:
        return ()
    return database_kept_fields(registered)


class _LeavesKeptFieldsOut:
    def resolve(self, model):
        kept = _kept_fields_of(model)
        named = sorted({model._meta.get_field(name).name for name in self.fields} & set(kept))
        if named:
            raise ValueError(
                _("%(condition)s names %(fields)s on %(model)s, which it leaves out - the database keeps them.")
                % {"condition": type(self).__name__, "fields": ", ".join(named), "model": model._meta.label}
            )
        leaving_out = copy.copy(self)
        leaving_out.exclude = [*self.exclude, *kept]
        return super(_LeavesKeptFieldsOut, leaving_out).resolve(model)


class AnyChange(_LeavesKeptFieldsOut, pgtrigger.AnyChange):
    """`pgtrigger.AnyChange`, never comparing the model's database-kept fields."""


class AnyDontChange(_LeavesKeptFieldsOut, pgtrigger.AnyDontChange):
    """`pgtrigger.AnyDontChange`, never comparing the model's database-kept fields."""


class AllChange(_LeavesKeptFieldsOut, pgtrigger.AllChange):
    """`pgtrigger.AllChange`, never comparing the model's database-kept fields."""


class AllDontChange(_LeavesKeptFieldsOut, pgtrigger.AllDontChange):
    """`pgtrigger.AllDontChange`, never comparing the model's database-kept fields."""


CHANGES = (pgtrigger.AnyChange, pgtrigger.AnyDontChange, pgtrigger.AllChange, pgtrigger.AllDontChange)


def _change_reads(condition, model, trigger):
    """The fields a change condition compares that it shouldn't - none when it compares every column
    after the row is written, which is every change and only one."""
    names = {field.name for field in model._meta.fields}
    excluded = {model._meta.get_field(name).name for name in condition.exclude}
    if condition.exclude_auto:
        excluded |= _auto_fields(model)
    compared = ({model._meta.get_field(name).name for name in condition.fields} or names) - excluded
    if compared == names and trigger.when == pgtrigger.After:
        return set()
    return compared


def _auto_fields(model):
    # The fields that take auto_now and auto_now_add - DateTimeField is a DateField.
    dated = [field for field in model._meta.fields if isinstance(field, (models.DateField, models.TimeField))]
    return {field.name for field in dated if field.auto_now or field.auto_now_add}


def _q_reads(condition, model):
    """The fields a `Q` condition reads, as `old__<field>`/`new__<field>` keys or `F()` values."""
    read = set()
    for child in condition.children:
        if isinstance(child, Q):
            read |= _q_reads(child, model)
            continue
        key, value = child
        paths = [key, value.name] if isinstance(value, F) else [key]
        # Each path is old__<field>... or new__<field>... - pgtrigger's Q and F read no other.
        read |= {model._meta.get_field(path.split("__")[1]).name for path in paths}
    return read


def _kept_fields_read(trigger, model):
    condition = trigger.get_condition(model)
    if isinstance(condition, _LeavesKeptFieldsOut):
        return []
    if isinstance(condition, CHANGES):
        read = _change_reads(condition, model, trigger)
    elif isinstance(condition, pgtrigger.Q):
        read = _q_reads(condition, model)
    else:
        # Plain SQL, or no condition at all - nothing isik can read.
        return []
    return [name for name in database_kept_fields(model) if name in read]


def kept_field_condition_problems(app_configs):
    """
    Each trigger on a model of `app_configs` whose condition compares a database-kept field, one
    sentence each. Comparing every column in an AFTER trigger is fine - the kept fields move only when
    another column changed. The system check `isik.E013` reports them.
    """
    labels = {config.label for config in app_configs}
    problems = []
    for model, trigger in pgtrigger.registered():
        if model._meta.app_label not in labels:
            continue
        read = _kept_fields_read(trigger, model)
        if read:
            problems.append(
                _(
                    "%(model)s's trigger %(trigger)s has a condition comparing %(fields)s, which the database "
                    "moves whenever any other column changes - leave them out of it."
                )
                % {"model": model._meta.label, "trigger": trigger.name, "fields": ", ".join(read)}
            )
    return problems
