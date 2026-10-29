import functools
import os
import time
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
from uuid import UUID

import pgtrigger
from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models, transaction
from django.db.models.fields.related import lazy_related_operation
from django.db.models.functions import Now
from django.db.models.signals import class_prepared
from django.utils.module_loading import import_string
from django_lifecycle import (
    AFTER_CREATE,
    AFTER_DELETE,
    AFTER_SAVE,
    AFTER_UPDATE,
    BEFORE_CREATE,
    BEFORE_DELETE,
    BEFORE_SAVE,
    BEFORE_UPDATE,
    LifecycleModelMixin,
)
from django_lifecycle.mixins import _bypass_state

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.common.utils.strings import snake_to_pascal
from isik.django.apps.common.generated import mark_generated
from isik.django.apps.common.skippable_validators import SkippableValidatorsMixin


def _check_pgtrigger_installed():
    # pgtrigger.register() below (called for every DatabaseTimestampsModel subclass) is a no-op without
    # pgtrigger's AppConfig - it's what makes the trigger registry migration-aware in the first
    # place. Without this check, subclassing BaseModel would just silently get no triggers.
    if not django_apps.is_installed("pgtrigger"):
        raise ImproperlyConfigured(
            _(
                "DatabaseTimestampsModel (and so BaseModel) requires 'pgtrigger' in INSTALLED_APPS - it "
                "maintains created_at/updated_at "
                "via database triggers, not Django's auto_now/auto_now_add. django-pgtrigger installs "
                "automatically as django-pghistory's dependency; add both to INSTALLED_APPS."
            )
        )


_check_pgtrigger_installed()


def uuid7():
    """
    A version 7 UUID (RFC 9562): the time it was made, in milliseconds since the epoch, in its first 48
    bits, then 74 random ones - so keys made later sort later, which an index's pages fill in order. The
    standard library has one only from Python 3.14.
    """
    milliseconds = time.time_ns() // 1_000_000
    random = int.from_bytes(os.urandom(10))
    value = (milliseconds & (1 << 48) - 1) << 80  # unix_ts_ms
    value |= 7 << 76  # version
    value |= (random >> 62 & 0xFFF) << 64  # rand_a
    value |= 0b10 << 62  # variant
    value |= random & (1 << 62) - 1  # rand_b
    return UUID(int=value)


@functools.cache
def _primary_key_function(path):
    return import_string(path)


def new_primary_key():
    """
    A new primary key for a `UUIDPrimaryKeyModel` - `ISIK_PRIMARY_KEY_DEFAULT`'s, a dotted path to a
    function returning a UUID (`"isik.django.apps.common.db.models.uuid7"`, say), `uuid.uuid4` unless
    set. Read when a key is made rather than written into each model's migration, so switching from one
    to the other is a setting, not a migration of every model.
    """
    return _primary_key_function(getattr(settings, "ISIK_PRIMARY_KEY_DEFAULT", "uuid.uuid4"))()


class UUIDPrimaryKeyModel(models.Model):
    """
    A UUID primary key - nothing to guess from, nothing to enumerate, no sequence to share. Random by
    default; `ISIK_PRIMARY_KEY_DEFAULT` picks another kind, see `new_primary_key()`.
    """

    id = models.UUIDField(
        primary_key=True,
        db_index=True,
        editable=False,
        default=new_primary_key,
        verbose_name=gettext_lazy("ID"),
        help_text=gettext_lazy("A random identifier, assigned when the row is created."),
        db_comment="A random identifier, assigned when the row is created.",
    )

    class Meta:
        abstract = True


class DatabaseTimestampsModel(models.Model):
    """
    `created_at`/`updated_at` kept by the database rather than by Django: `db_default=Now()` on insert,
    a trigger refusing any change to `created_at`, and a BEFORE UPDATE trigger stamping `updated_at`
    whenever the row changes - so `QuerySet.update()`, `bulk_update()` and raw SQL keep them true too,
    none of which `auto_now` touches. A write changing nothing leaves `updated_at` as it was, and so
    records no history event; `touch_rows()` moves it for a change the row doesn't show. A trigger
    condition of a project's own leaves it out - see `db/conditions.py`. Needs `pgtrigger` installed;
    the triggers attach to every concrete subclass.
    """

    created_at = models.DateTimeField(
        db_default=Now(),
        db_index=True,
        editable=False,
        verbose_name=gettext_lazy("Created At"),
        help_text=gettext_lazy("When the row was created, by the database's clock. Never changes."),
        db_comment="When the row was created, by the database's clock. A trigger refuses any change to it.",
    )
    # Unlike auto_now, stamped by a BEFORE UPDATE trigger (see _timestamp_triggers() below) whenever
    # the row changes - update_fields does not gate it. save(update_fields=["name"]) advances
    # updated_at when name changed; naming "updated_at" in update_fields is harmless but no longer
    # necessary, and a stale value saved back is held where the row had it.
    updated_at = models.DateTimeField(
        db_default=Now(),
        db_index=True,
        editable=False,
        verbose_name=gettext_lazy("Updated At"),
        help_text=gettext_lazy("When the row last changed, by the database's clock."),
        db_comment="When the row last changed, by the database's clock. Stamped by a trigger whenever the row changes.",
    )

    class Meta:
        abstract = True


class VersionedModel(models.Model):
    """
    A `version` the database keeps: 1 on insert, and `OLD.version + 1` by a BEFORE UPDATE trigger
    whenever the row changes, whatever the write set it to - `QuerySet.update()`, `bulk_update()` and
    raw SQL included. A write changing nothing leaves it as it was; `touch_rows()` moves it for a change
    the row doesn't show. A concurrency token that is a plain count rather than a time; see
    `db/concurrency.py`.
    Needs `pgtrigger` installed, like `DatabaseTimestampsModel`; the trigger attaches to every
    concrete subclass.
    """

    version = models.PositiveBigIntegerField(
        db_default=1,
        editable=False,
        verbose_name=gettext_lazy("Version"),
        help_text=gettext_lazy("How many times the row has changed, counting the write that created it."),
        db_comment="How many times the row has changed. A trigger adds one whenever it does.",
    )

    class Meta:
        abstract = True


class FullCleanOnSaveModel(SkippableValidatorsMixin, LifecycleModelMixin, models.Model):
    """
    `save()` runs `full_clean()` and django-lifecycle's hooks, in that model's order: BEFORE_* hooks,
    then `full_clean()`, then the write, then AFTER_* hooks. A field a BEFORE_* hook sets still reaches
    the database when `update_fields` didn't name it. `SKIP_FULL_CLEAN = True` (or `skip_full_clean()`)
    skips validation; `save(_skip_hooks=True)` skips the hooks. Validators are skippable per call - see
    `SkippableValidatorsMixin`.

    django-lifecycle's own `bypass_hooks_for(models)` works here too, on saves and on deletes. Both
    methods below are replacements rather than extensions of `LifecycleModelMixin`'s, so each asks
    about the bypass itself; without that the context manager would run and suppress nothing. Deletes
    are covered because the name says hooks rather than saves - `LifecycleModelMixin.delete` does not
    consult it upstream. Skipping hooks never skips `full_clean()`: whether a row is valid is not a
    question about hooks, and only `SKIP_FULL_CLEAN` answers it.

    Don't put a `classproperty` with a query-building body on a subclass of this - use a plain
    `classmethod` instead. `django_lifecycle`'s `LifecycleModelMixin` scans class attributes via
    `getattr(cls, name)` on every instantiation to find hook methods, which evaluates a
    `classproperty` eagerly as a side effect regardless of whether anything asked for it. If that
    property builds a queryset by instantiating the same model, this recurses infinitely - a
    `django_lifecycle` behavior, not something fixable from here, just a documented trap.
    """

    SKIP_FULL_CLEAN = False

    @transaction.atomic
    def save(self, *args, **kwargs):
        skip_hooks = kwargs.pop("_skip_hooks", None) or _bypass_state.is_bypassed_for(type(self))
        save = super(LifecycleModelMixin, self).save

        if skip_hooks:
            if not self.SKIP_FULL_CLEAN:
                self.full_clean()
            save(*args, **kwargs)
            return

        self._clear_watched_fk_model_cache()
        is_new = self._state.adding

        # Snapshotted before the hooks below run, so that if a BEFORE_CREATE/BEFORE_UPDATE/
        # BEFORE_SAVE hook (or full_clean()'s own field cleaning) sets a field that isn't in the
        # caller's update_fields, its new value still reaches the database instead of being
        # silently discarded by the restricted UPDATE below.
        requested_update_fields = kwargs.get("update_fields")
        before_hooks = self._field_values() if requested_update_fields is not None else None

        if is_new:
            self._run_hooked_methods(BEFORE_CREATE, **kwargs)
        else:
            self._run_hooked_methods(BEFORE_UPDATE, **kwargs)

        self._run_hooked_methods(BEFORE_SAVE, **kwargs)

        if not self.SKIP_FULL_CLEAN:
            self.full_clean()

        if before_hooks is not None:
            kwargs["update_fields"] = self._widen_update_fields(requested_update_fields, before_hooks)

        save(*args, **kwargs)
        self._run_hooked_methods(AFTER_SAVE, **kwargs)

        if is_new:
            self._run_hooked_methods(AFTER_CREATE, **kwargs)
        else:
            self._run_hooked_methods(AFTER_UPDATE, **kwargs)

        transaction.on_commit(self._reset_initial_state)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        if _bypass_state.is_bypassed_for(type(self)):
            return super(LifecycleModelMixin, self).delete(*args, **kwargs)
        self._run_hooked_methods(BEFORE_DELETE, **kwargs)
        deleted = super(LifecycleModelMixin, self).delete(*args, **kwargs)
        self._run_hooked_methods(AFTER_DELETE, **kwargs)
        return deleted

    def update(self, **kwargs):
        skip_hooks = kwargs.pop("_skip_hooks", None)
        update_fields = list(kwargs.keys())
        for key, val in kwargs.items():
            setattr(self, key, val)
        return self.save(_skip_hooks=skip_hooks, update_fields=update_fields)

    def _field_values(self):
        return {field.name: getattr(self, field.attname) for field in self._meta.concrete_fields}

    def _widen_update_fields(self, requested_fields, before):
        after = self._field_values()
        changed_by_hooks = {name for name, value in before.items() if after[name] != value}
        return list({*requested_fields, *changed_by_hooks})

    @contextmanager
    def skip_full_clean(self):
        original_value = self.SKIP_FULL_CLEAN
        self.SKIP_FULL_CLEAN = True
        try:
            yield
        finally:
            self.SKIP_FULL_CLEAN = original_value

    class Meta:
        abstract = True


class ReprModel(models.Model):
    """`STR`/`REPR` as format strings over `self` - `str()` falls back to `repr()` without an `STR`."""

    STR = None
    REPR = "{self.__class__.__name__}(id={self.pk})"

    def __repr__(self):
        return self.REPR.format(self=self)

    def __str__(self):
        return self.STR.format(self=self) if self.STR else self.__repr__()

    class Meta:
        abstract = True


class BaseModel(UUIDPrimaryKeyModel, DatabaseTimestampsModel, FullCleanOnSaveModel, ReprModel):
    """
    Every model mixin above composed together - see each one's docstring: UUIDPrimaryKeyModel,
    DatabaseTimestampsModel, FullCleanOnSaveModel, ReprModel. Compose them directly instead for a
    model that wants only some.
    """

    FIELDS = ["id", "created_at", "updated_at"]

    def as_queryset(self):
        return self.__class__.objects.filter(pk=self.pk)

    class Meta:
        abstract = True


def database_kept_fields(model):
    """
    The names of `model`'s fields the database keeps on every change - `version` on a `VersionedModel`,
    `updated_at` on a `DatabaseTimestampsModel` - in that order, or `()`.
    """
    names = []
    if issubclass(model, VersionedModel):
        names.append("version")
    if issubclass(model, DatabaseTimestampsModel):
        names.append("updated_at")
    return tuple(names)


def _row_changes(model, kept):
    """
    The SQL for "this UPDATE changes the row": a column other than the `kept` ones differs from OLD, or a
    kept one was moved forward - `touch_rows()`, or another kept field's trigger having moved it.
    """
    quote = connection.ops.quote_name
    # A generated column isn't computed yet in a BEFORE trigger's NEW - it follows the others anyway.
    derived = [field.name for field in model._meta.concrete_fields if field.generated]
    others = pgtrigger.AnyChange(exclude=[*kept, *derived]).resolve(model)
    columns = [quote(model._meta.get_field(name).column) for name in kept]
    forward = [f"NEW.{column} > OLD.{column}" for column in columns]
    return " OR ".join([f"({others})", *forward])


class _KeptFieldTrigger(pgtrigger.Trigger):
    """
    A BEFORE UPDATE trigger moving `field` on with `step` (SQL over `{column}`) when the row changes,
    and holding it at OLD when it doesn't - see `_row_changes()`. It fires on every UPDATE: held at OLD,
    a stale value a write sends back can't take the field backwards. `kept` is every database-kept
    field of the model, named when it's registered: a migration renders the trigger against a model of
    its own state, which is no subclass of the bases that say which they are.
    """

    when = pgtrigger.Before
    operation = pgtrigger.Update

    def __init__(self, *, name, field, step, kept):
        self.field, self.step, self.kept = field, step, kept
        super().__init__(name=name)

    def get_func(self, model):
        # The column, not the field name: a subclass giving the field a db_column would otherwise leave
        # the trigger assigning to a column that doesn't exist, failing every UPDATE.
        column = connection.ops.quote_name(model._meta.get_field(self.field).column)
        return (
            f"IF {_row_changes(model, self.kept)} THEN NEW.{column} = {self.step.format(column=column)}; "
            f"ELSE NEW.{column} = OLD.{column}; END IF; RETURN NEW;"
        )


def _timestamp_triggers(model):
    return [
        # db_default=Now() only fires on INSERT - nothing stops a later UPDATE from changing
        # created_at, so it also needs protecting at the row level.
        pgtrigger.ReadOnly(name="protect_created_at", fields=["created_at"]),
        # db_default=Now() covers the initial value; this keeps it current on every UPDATE that changes
        # the row, including QuerySet.update()/bulk_update() and raw SQL, none of which auto_now touches.
        # clock_timestamp(), not NOW(): NOW() is when the transaction began, so one that began first
        # and wrote last stamped the row earlier than the write before it.
        # GREATEST(..., OLD + 1 microsecond) makes every change move it strictly forward, which is
        # what lets updated_at be a concurrency token (see db/concurrency.py): a write that left it
        # equal - two in the same microsecond, the server's clock stepping back - would leave a
        # stale If-Match matching. A microsecond is timestamptz's resolution, the smallest step
        # there is. After a step back the column runs ahead of the clock, a microsecond per write,
        # until the clock catches up - it never goes backwards, and nothing reads it as wall time
        # more precisely than that.
        _KeptFieldTrigger(
            name="stamp_updated_at",
            field="updated_at",
            step="GREATEST(clock_timestamp(), OLD.{column} + interval '1 microsecond')",
            kept=database_kept_fields(model),
        ),
    ]


def _version_triggers(model):
    # OLD, not NEW: a write that sets version itself - a stale one sent back, say - still only moves it
    # on by one from what the row held. Postgres runs BEFORE triggers in name order, so this one decides
    # before stamp_updated_at, which sees the version it moved and moves with it. A project's own BEFORE
    # trigger sorting between the two that changes a column on a write that changed nothing else moves
    # updated_at without the version.
    return [
        _KeptFieldTrigger(
            name="bump_version", field="version", step="OLD.{column} + 1", kept=database_kept_fields(model)
        )
    ]


def touch_rows(queryset):
    """
    Moves the database-kept fields (`database_kept_fields()`) of every row in `queryset` on, as a change
    to the row would - for a write the row itself doesn't show, which should still move its version
    and `updated_at`. An UPDATE that changes nothing leaves them as they are. Returns the number of
    rows, as `QuerySet.update()` does.

        touch_rows(Invoice.objects.filter(pk=invoice.pk))
    """
    model = queryset.model
    kept = database_kept_fields(model)
    if not kept:
        raise TypeError(
            _("touch_rows() moves version and updated_at, and %(model)s has neither.") % {"model": model._meta.label}
        )
    # One kept field moved forward is enough - the triggers move every kept field together.
    name = kept[0]
    step = 1 if name == "version" else timedelta(microseconds=1)
    return queryset.update(**{name: models.F(name) + step})


def _register_triggers(sender, **kwargs):
    # Declaring these on DatabaseTimestampsModel's own Meta.triggers wouldn't reach subclasses - Django only
    # inherits an abstract base's Meta into a subclass that writes `class Meta(BaseModel.Meta)`,
    # and nothing here does (they declare their own Meta for app_label/ordering/etc.). Attaching
    # via pgtrigger.register() on every concrete subclass instead needs no such cooperation - Django
    # sends class_prepared for concrete models only. Not a proxy, though: it shares its concrete model's
    # table, whose triggers are already there - registering them again under the same names is refused.
    if sender._meta.proxy:
        return
    if issubclass(sender, DatabaseTimestampsModel):
        pgtrigger.register(*_timestamp_triggers(sender))(sender)
    if issubclass(sender, VersionedModel):
        pgtrigger.register(*_version_triggers(sender))(sender)


class_prepared.connect(_register_triggers, dispatch_uid="isik_base_model_timestamp_triggers")


# [(owner model, relation name, proxy model, trigger)] - one per relation a model's concurrency token
# covers (`concurrency_relations`, see db/concurrency.py), for isik.W002 to check is installed.
relation_locks = []


def _forward(model, key):
    """The SET list of an UPDATE moving `model`'s first database-kept field forward - else its key to itself."""
    kept = database_kept_fields(model)
    if not kept:
        return f"{key} = {key}"
    column = connection.ops.quote_name(model._meta.get_field(kept[0]).column)
    step = "1" if kept[0] == "version" else "interval '1 microsecond'"
    return f"{column} = {column} + {step}"


def _relation_lock_trigger(owner, name, owner_key):
    """
    The trigger on the table a relation's rows live in - a many-to-many's through table, or the table of
    a model pointing at `owner` - where `owner_key` is the column naming the owner row.
    """
    quote = connection.ops.quote_name
    table, key = quote(owner._meta.db_table), quote(owner_key.target_field.column)
    owner_row = f"{key} = %s.{quote(owner_key.column)}"
    if getattr(owner, "concurrency_strategy", None) == "column":
        # The column strategy: an UPDATE of the owner row moving its database-kept fields forward, as
        # touch_rows() does - its own BEFORE UPDATE triggers move them only for a change, and a row of
        # this table is one the owner's row doesn't show. A token a trigger of the project's own keeps
        # gets its UPDATE all the same. Those triggers only assign NEW and never touch this table, so
        # it's one UPDATE of the owner per row changed here, never a loop. The UPDATE locks the owner
        # row as the lock below does.
        trigger_name, statement = f"bump_{name}_owner", f"UPDATE {table} SET {_forward(owner, key)} WHERE {owner_row};"
    else:
        # The computed strategy: locks the owner row and writes nothing. Adding a row already takes this
        # lock, through its foreign key's check; removing one doesn't - and either has to wait for a
        # write holding the owner FOR UPDATE, which compared a version that counts this relation, so
        # the two can't interleave.
        trigger_name, statement = f"lock_{name}_owner", f"PERFORM 1 FROM {table} WHERE {owner_row} FOR KEY SHARE;"
    return pgtrigger.Trigger(
        name=trigger_name,
        when=pgtrigger.After,
        operation=pgtrigger.Insert | pgtrigger.Update | pgtrigger.Delete,
        # An update that moves a row between owners reaches both.
        func=(
            f"IF TG_OP <> 'INSERT' THEN {statement % 'OLD'} END IF; "
            f"IF TG_OP <> 'DELETE' THEN {statement % 'NEW'} END IF; "
            "RETURN NULL;"
        ),
    )


def _register_relation_lock(owner, name, table, owner_key):
    trigger = _relation_lock_trigger(owner, name, owner_key)

    # A proxy over the model whose table holds the relation's rows, in the owner's app: how pgtrigger
    # triggers an auto-created many-to-many table, and a model of another app's, and puts the trigger in
    # the owner app's migrations.
    class Meta:
        proxy = True
        app_label = owner._meta.app_label
        triggers = [trigger]

    proxy_name = f"{owner.__name__}{snake_to_pascal(name)}ConcurrencyLock"
    proxy = mark_generated(type(proxy_name, (table,), {"__module__": owner.__module__, "Meta": Meta}), "lock")
    relation_locks.append((owner, name, proxy, trigger))


def _lock_many_to_many(owner, _related, through, relation):
    _register_relation_lock(owner, relation.name, through, through._meta.get_field(relation.m2m_field_name()))


def _lock_pointing_key(table, owner, key):
    # Named by the accessor the owner reaches the rows with - `discount_set`, or a related_name.
    name = key.remote_field.get_accessor_name()
    if name in getattr(owner, "concurrency_relations", ()):
        _register_relation_lock(owner, name, table, key)


def _register_relation_locks(sender, **kwargs):
    # A model swapped out for the project's own has no table - nothing to lock, and what its keys point at may be
    # an app a migration's state doesn't hold yet.
    if sender._meta.swapped:
        return
    for name in getattr(sender, "concurrency_relations", ()):
        relation = next((field for field in sender._meta.local_many_to_many if field.name == name), None)
        # A name that isn't one is a reverse relation, registered from its own side below - or
        # concurrency_token_problems()'s to report, rather than an import that fails.
        if relation is None:
            continue
        # Once the related model and the through model are both loaded - either may be named by a
        # string, and only then does the relation know which of the through model's keys is the owner's.
        lazy_related_operation(
            partial(_lock_many_to_many, relation=relation),
            sender,
            relation.remote_field.model,
            relation.remote_field.through,
        )
    # Each foreign key and one-to-one of this model is a reverse relation of the model it points at,
    # which may name it - once that model is loaded, too.
    for key in sender._meta.local_fields:
        if key.many_to_one or key.one_to_one:
            lazy_related_operation(partial(_lock_pointing_key, key=key), sender, key.remote_field.model)


class_prepared.connect(_register_relation_locks, dispatch_uid="isik_concurrency_relation_locks")
