from contextlib import contextmanager
from uuid import uuid4

from django.db import models, transaction
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


class BaseModel(SkippableValidatorsMixin, LifecycleModelMixin, models.Model):
    STR = None
    REPR = "{self.__class__.__name__}(id={self.id})"
    FIELDS = ["id", "created_at", "updated_at"]
    SKIP_FULL_CLEAN = False

    id = models.UUIDField(primary_key=True, db_index=True, editable=False, default=uuid4, verbose_name=_("ID"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Created At"))
    updated_at = models.DateTimeField(auto_now=True, db_index=True, verbose_name=_("Updated At"))

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
        skip_hooks = kwargs.pop("_skip_hooks", False)
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
