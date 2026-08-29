from contextlib import contextmanager
from uuid import uuid4

import pgtrigger
from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.models.functions import Now
from django.db.models.signals import class_prepared
from django.utils.translation import gettext as _
from django_lifecycle import (
    AFTER_CREATE,
    AFTER_SAVE,
    AFTER_UPDATE,
    BEFORE_CREATE,
    BEFORE_SAVE,
    BEFORE_UPDATE,
    LifecycleModelMixin,
)

from isik.django.apps.common.skippable_validators import SkippableValidatorsMixin


def _check_pgtrigger_installed():
    # pgtrigger.register() below (called for every BaseModel subclass) is a no-op without
    # pgtrigger's AppConfig - it's what makes the trigger registry migration-aware in the first
    # place. Without this check, subclassing BaseModel would just silently get no triggers.
    if not django_apps.is_installed("pgtrigger"):
        raise ImproperlyConfigured(
            "BaseModel requires 'pgtrigger' in INSTALLED_APPS - it maintains created_at/updated_at "
            "via database triggers, not Django's auto_now/auto_now_add. django-pgtrigger installs "
            "automatically as django-pghistory's dependency; add both to INSTALLED_APPS."
        )


_check_pgtrigger_installed()


class BaseModel(SkippableValidatorsMixin, LifecycleModelMixin, models.Model):
    """
    Don't put a `classproperty` with a query-building body on a subclass of this - use a plain
    `classmethod` instead. `django_lifecycle`'s `LifecycleModelMixin` scans class attributes via
    `getattr(cls, name)` on every instantiation to find hook methods, which evaluates a
    `classproperty` eagerly as a side effect regardless of whether anything asked for it. If that
    property builds a queryset by instantiating the same model, this recurses infinitely - a
    `django_lifecycle` behavior, not something fixable from here, just a documented trap.
    """

    STR = None
    REPR = "{self.__class__.__name__}(id={self.id})"
    FIELDS = ["id", "created_at", "updated_at"]
    SKIP_FULL_CLEAN = False

    id = models.UUIDField(primary_key=True, db_index=True, editable=False, default=uuid4, verbose_name=_("ID"))
    created_at = models.DateTimeField(db_default=Now(), db_index=True, editable=False, verbose_name=_("Created At"))
    updated_at = models.DateTimeField(db_default=Now(), db_index=True, editable=False, verbose_name=_("Updated At"))

    @transaction.atomic
    def save(self, *args, **kwargs):
        skip_hooks = kwargs.pop("_skip_hooks", False)
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

    def update(self, **kwargs):
        skip_hooks = kwargs.pop("_skip_hooks", False)  # pragma: no mutate
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

    def as_queryset(self):
        return self.__class__.objects.filter(id=self.id)

    @contextmanager
    def skip_full_clean(self):
        original_value = self.SKIP_FULL_CLEAN
        self.SKIP_FULL_CLEAN = True
        try:
            yield
        finally:
            self.SKIP_FULL_CLEAN = original_value

    def __repr__(self):
        return self.REPR.format(self=self)

    def __str__(self):
        return self.STR.format(self=self) if self.STR else self.__repr__()

    class Meta:
        abstract = True


def _timestamp_triggers():
    return [
        # db_default=Now() only fires on INSERT - nothing stops a later UPDATE from changing
        # created_at, so it also needs protecting at the row level.
        pgtrigger.ReadOnly(name="protect_created_at", fields=["created_at"]),
        # db_default=Now() covers the initial value; this keeps it current on every UPDATE,
        # including QuerySet.update()/bulk_update() and raw SQL, none of which auto_now touches.
        pgtrigger.Trigger(
            name="stamp_updated_at",
            when=pgtrigger.Before,
            operation=pgtrigger.Update,
            func="NEW.updated_at = NOW(); RETURN NEW;",
        ),
    ]


def _register_timestamp_triggers(sender, **kwargs):
    # Declaring these on BaseModel's own Meta.triggers wouldn't reach subclasses - Django only
    # inherits an abstract base's Meta into a subclass that writes `class Meta(BaseModel.Meta)`,
    # and nothing here does (they declare their own Meta for app_label/ordering/etc.). Attaching
    # via pgtrigger.register() on every concrete subclass instead needs no such cooperation.
    if issubclass(sender, BaseModel) and not sender._meta.abstract:  # pragma: no mutate
        # class_prepared fires exactly once per model class, ever - a mutation here is provably
        # caught by test_timestamp_triggers_are_registered_on_every_concrete_basemodel_subclass
        # under plain pytest, but not under mutmut: whichever variant is active the one time this
        # runs for a given class in a worker process is what that class is permanently stuck with,
        # regardless of which mutant mutmut later considers "active" for a later test.
        pgtrigger.register(*_timestamp_triggers())(sender)


class_prepared.connect(_register_timestamp_triggers, dispatch_uid="isik_base_model_timestamp_triggers")
