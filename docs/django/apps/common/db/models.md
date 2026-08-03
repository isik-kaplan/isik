# models

`BaseModel` is the abstract base most models in this codebase inherit from: a UUID primary key,
`created_at`/`updated_at` timestamps, `django-lifecycle` hooks (BEFORE/AFTER CREATE/UPDATE/SAVE)
wired into `save()`, and field validators wrapped via `SkippableValidatorsMixin` so they can be
selectively bypassed. `full_clean()` runs on every `save()` unless bypassed.

```python
from isik.django.apps.common.db import BaseModel

class Widget(BaseModel):
    name = models.CharField(max_length=100)
    count = models.IntegerField(default=0, validators=[positive_only])

widget = Widget.objects.create(name="bolt", count=3)
widget.update(count=5)           # setattr(...) + save(update_fields=[...])
with widget.skip_full_clean():
    widget.count = -5
    widget.save()                 # bypasses full_clean() for calls inside the block
```

- `save(_skip_hooks=True)` (also reachable as `update(..., _skip_hooks=True)`) skips only the
  lifecycle hooks — `full_clean()` still runs unless also inside `skip_full_clean()`.
- `skip_full_clean()` is a context manager with no field granularity; to bypass only specific
  validators (not the whole `full_clean()`), use `SkipFieldValidators`/`SkipNamedValidators` from
  `skippable_validators` instead.
- `FIELDS = ["id", "created_at", "updated_at"]` is a class attribute `BaseAdmin` reads to
  auto-append readonly/list-display fields.
- `__str__` falls back to `REPR` (`"{self.__class__.__name__}(id={self.id})"`) unless the
  subclass sets `STR` to its own format string.
- Don't put a `classproperty` with a query-building body on a subclass - use a plain `classmethod`
  instead. `django_lifecycle`'s `LifecycleModelMixin` scans class attributes on every
  instantiation to find hook methods, which evaluates a `classproperty` eagerly as a side effect;
  if that property builds a queryset by instantiating the same model, this recurses infinitely.
  This is a `django_lifecycle` behavior, not something `BaseModel` can fix - just a trap worth
  knowing about.
