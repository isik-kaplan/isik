from contextlib import contextmanager
from uuid import uuid4

import pgtrigger
from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models, transaction
from django.db.models.fields.related import lazy_related_operation
from django.db.models.functions import Now
from django.db.models.signals import class_prepared
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


class UUIDPrimaryKeyModel(models.Model):
    """A random UUID primary key - nothing to guess from, nothing to enumerate, no sequence to share."""

    id = models.UUIDField(
        primary_key=True,
        db_index=True,
        editable=False,
        default=uuid4,
        verbose_name=gettext_lazy("ID"),
        help_text=gettext_lazy("A random identifier, assigned when the row is created."),
        db_comment="A random identifier, assigned when the row is created.",
    )

    class Meta:
        abstract = True


class DatabaseTimestampsModel(models.Model):
    """
    `created_at`/`updated_at` kept by the database rather than by Django: `db_default=Now()` on insert,
    a trigger refusing any change to `created_at`, and a BEFORE UPDATE trigger stamping `updated_at` -
    so `QuerySet.update()`, `bulk_update()` and raw SQL keep them true too, none of which `auto_now`
    touches. Needs `pgtrigger` installed; the triggers attach to every concrete subclass.
    """

    created_at = models.DateTimeField(
        db_default=Now(),
        db_index=True,
        editable=False,
        verbose_name=gettext_lazy("Created At"),
        help_text=gettext_lazy("When the row was created, by the database's clock. Never changes."),
        db_comment="When the row was created, by the database's clock. A trigger refuses any change to it.",
    )
    # Unlike auto_now, stamped by a BEFORE UPDATE trigger (see _timestamp_triggers() below) that
    # fires unconditionally - update_fields does not gate it. save(update_fields=["name"]) still
    # advances updated_at; explicitly naming "updated_at" in update_fields is harmless but no
    # longer necessary.
    updated_at = models.DateTimeField(
        db_default=Now(),
        db_index=True,
        editable=False,
        verbose_name=gettext_lazy("Updated At"),
        help_text=gettext_lazy("When the row last changed, by the database's clock."),
        db_comment="When the row last changed, by the database's clock. Stamped by a trigger on every update.",
    )

    class Meta:
        abstract = True


class VersionedModel(models.Model):
    """
    A `version` the database keeps: 1 on insert, and `OLD.version + 1` on every UPDATE by a BEFORE
    UPDATE trigger, whatever the write set it to - `QuerySet.update()`, `bulk_update()` and raw SQL
    included. A concurrency token that is a plain count rather than a time; see `db/concurrency.py`.
    Needs `pgtrigger` installed, like `DatabaseTimestampsModel`; the trigger attaches to every
    concrete subclass.
    """

    version = models.PositiveBigIntegerField(
        db_default=1,
        editable=False,
        verbose_name=gettext_lazy("Version"),
        help_text=gettext_lazy("How many times the row has been written, counting the write that created it."),
        db_comment="How many times the row has been written. A trigger adds one on every update.",
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


def _timestamp_triggers(model):
    # The column, not the field name: a subclass giving updated_at a db_column would otherwise leave
    # the trigger assigning to a column that doesn't exist, failing every UPDATE.
    updated_at = connection.ops.quote_name(model._meta.get_field("updated_at").column)
    return [
        # db_default=Now() only fires on INSERT - nothing stops a later UPDATE from changing
        # created_at, so it also needs protecting at the row level.
        pgtrigger.ReadOnly(name="protect_created_at", fields=["created_at"]),
        # db_default=Now() covers the initial value; this keeps it current on every UPDATE,
        # including QuerySet.update()/bulk_update() and raw SQL, none of which auto_now touches.
        # clock_timestamp(), not NOW(): NOW() is when the transaction began, so one that began first
        # and wrote last stamped the row earlier than the write before it.
        # GREATEST(..., OLD + 1 microsecond) makes every UPDATE move it strictly forward, which is
        # what lets updated_at be a concurrency token (see db/concurrency.py): a write that left it
        # equal - two in the same microsecond, the server's clock stepping back - would leave a
        # stale If-Match matching. A microsecond is timestamptz's resolution, the smallest step
        # there is. After a step back the column runs ahead of the clock, a microsecond per write,
        # until the clock catches up - it never goes backwards, and nothing reads it as wall time
        # more precisely than that.
        pgtrigger.Trigger(
            name="stamp_updated_at",
            when=pgtrigger.Before,
            operation=pgtrigger.Update,
            func=(
                f"NEW.{updated_at} = GREATEST(clock_timestamp(), OLD.{updated_at} + interval '1 microsecond'); "
                "RETURN NEW;"
            ),
        ),
    ]


def _version_triggers(model):
    # The column, not the field name - see _timestamp_triggers().
    version = connection.ops.quote_name(model._meta.get_field("version").column)
    return [
        # OLD, not NEW: a write that sets version itself - a stale one sent back, say - still only
        # moves it on by one from what the row held.
        pgtrigger.Trigger(
            name="bump_version",
            when=pgtrigger.Before,
            operation=pgtrigger.Update,
            func=f"NEW.{version} = OLD.{version} + 1; RETURN NEW;",
        ),
    ]


def _register_triggers(sender, **kwargs):
    # Declaring these on DatabaseTimestampsModel's own Meta.triggers wouldn't reach subclasses - Django only
    # inherits an abstract base's Meta into a subclass that writes `class Meta(BaseModel.Meta)`,
    # and nothing here does (they declare their own Meta for app_label/ordering/etc.). Attaching
    # via pgtrigger.register() on every concrete subclass instead needs no such cooperation - Django
    # sends class_prepared for concrete models only.
    if issubclass(sender, DatabaseTimestampsModel):
        pgtrigger.register(*_timestamp_triggers(sender))(sender)
    if issubclass(sender, VersionedModel):
        pgtrigger.register(*_version_triggers(sender))(sender)


class_prepared.connect(_register_triggers, dispatch_uid="isik_base_model_timestamp_triggers")


# [(owner model, relation name, proxy model, trigger)] - one per relation a model's concurrency token
# covers (`concurrency_relations`, see db/concurrency.py), for isik.W002 to check is installed.
relation_locks = []


def _relation_lock_trigger(owner, relation, through):
    quote = connection.ops.quote_name
    owner_key = through._meta.get_field(relation.m2m_field_name())
    table, key = quote(owner._meta.db_table), quote(owner_key.target_field.column)
    owner_row = f"{key} = %s.{quote(owner_key.column)}"
    if getattr(owner, "concurrency_strategy", None) == "column":
        # The column strategy: an UPDATE of the owner row that changes nothing itself, so the owner's own
        # BEFORE UPDATE triggers move its token - and updated_at with it. Those only assign NEW and never
        # touch this table, so it's one UPDATE of the owner per row changed here, never a loop. The
        # UPDATE locks the owner row as the lock below does.
        name, statement = f"bump_{relation.name}_owner", f"UPDATE {table} SET {key} = {key} WHERE {owner_row};"
    else:
        # The computed strategy: locks the owner row and writes nothing. Adding a row already takes this
        # lock, through its foreign key's check; removing one doesn't - and either has to wait for a
        # write holding the owner FOR UPDATE, which compared a version that counts this relation, so
        # the two can't interleave.
        name, statement = f"lock_{relation.name}_owner", f"PERFORM 1 FROM {table} WHERE {owner_row} FOR KEY SHARE;"
    return pgtrigger.Trigger(
        name=name,
        when=pgtrigger.After,
        operation=pgtrigger.Insert | pgtrigger.Update | pgtrigger.Delete,
        # An update that moves a row between owners reaches both.
        func=(
            f"IF TG_OP <> 'INSERT' THEN {statement % 'OLD'} END IF; "
            f"IF TG_OP <> 'DELETE' THEN {statement % 'NEW'} END IF; "
            "RETURN NULL;"
        ),
    )


def _register_relation_lock(owner, relation, through):
    trigger = _relation_lock_trigger(owner, relation, through)

    # A proxy over the through model, in the owner's app: how pgtrigger triggers an auto-created
    # many-to-many table and puts the trigger in that app's migrations.
    class Meta:
        proxy = True
        app_label = owner._meta.app_label
        triggers = [trigger]

    name = f"{owner.__name__}{snake_to_pascal(relation.name)}ConcurrencyLock"
    proxy = type(name, (through,), {"__module__": owner.__module__, "Meta": Meta})
    relation_locks.append((owner, relation.name, proxy, trigger))


def _register_relation_locks(sender, **kwargs):
    for name in getattr(sender, "concurrency_relations", ()):
        relation = next((field for field in sender._meta.local_many_to_many if field.name == name), None)
        # Anything else is concurrency_token_problems()'s to report, rather than an import that fails.
        if relation is None:
            continue
        # Once the related model and the through model are both loaded - either may be named by a
        # string, and only then does the relation know which of the through model's keys is the owner's.
        lazy_related_operation(
            lambda owner, _related, through, relation=relation: _register_relation_lock(owner, relation, through),
            sender,
            relation.remote_field.model,
            relation.remote_field.through,
        )


class_prepared.connect(_register_relation_locks, dispatch_uid="isik_concurrency_relation_locks")
