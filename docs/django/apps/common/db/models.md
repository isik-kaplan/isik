# models

`BaseModel` is four abstract models composed together, each usable alone:

| Model | Adds |
|---|---|
| `UUIDPrimaryKeyModel` | a random `uuid4` primary key |
| `DatabaseTimestampsModel` | `created_at`/`updated_at` kept by the database (needs `pgtrigger`) |
| `FullCleanOnSaveModel` | `full_clean()` and `django-lifecycle` hooks on every `save()`, skippable validators, `update()`, `skip_full_clean()` |
| `ReprModel` | `STR`/`REPR` format strings for `str()`/`repr()` |

Not part of `BaseModel`, and added beside it when wanted:

| Model | Adds |
|---|---|
| `VersionedModel` | a `version` the database counts up on every UPDATE (needs `pgtrigger`) |

```python
from isik.django.apps.common.db import DatabaseTimestampsModel, FullCleanOnSaveModel

class Event(DatabaseTimestampsModel):          # Django's own integer pk, no validation on save
    ...

class Setting(FullCleanOnSaveModel):           # validated on save, nothing else
    ...
```

Every field isik declares carries a `help_text` (translatable) and a `db_comment` (plain English, so
the migration doesn't depend on the language it was generated in).

`BaseModel` itself, the abstract base most models in this codebase inherit from: a UUID primary key,
`created_at`/`updated_at` timestamps, `django-lifecycle` hooks (BEFORE/AFTER CREATE/UPDATE/SAVE)
wired into `save()`, and field validators wrapped via `SkippableValidatorsMixin` so they can be
selectively bypassed. `full_clean()` runs on every `save()` unless bypassed.

`created_at`/`updated_at` are maintained by the database, not Django: `created_at` gets
`db_default=Now()` plus a trigger refusing any UPDATE that changes it, and `updated_at` is stamped
by a `BEFORE UPDATE` trigger on every UPDATE, including `QuerySet.update()`/`bulk_update()`/raw
SQL - not just `Model.save()`, the only thing `auto_now`/`auto_now_add` ever covered. Requires
`pgtrigger` in `INSTALLED_APPS` (`django-pghistory` already depends on it) - `DatabaseTimestampsModel` raises
`ImproperlyConfigured` at import time if it's missing. The trigger stamps the column `updated_at` is
stored in, so a subclass can give it a `db_column`.

The stamp is the time of the write itself (`clock_timestamp()`), and always at least a microsecond
later than the value it replaces: `GREATEST(clock_timestamp(), OLD.updated_at + interval '1
microsecond')`. It isn't `NOW()`, which is when the transaction began: a transaction that began first
and wrote last would stamp the row earlier than the write before it, so `updated_at` would go
backwards. And it never stays where it was - two writes in the same microsecond, or a server clock
stepping back, still move it on. That makes `updated_at` a [concurrency token](concurrency.md): a
version a write can name, and be refused for naming once it's stale. After a step back the column runs
ahead of the clock, a microsecond per write, until the clock catches up.

Unlike `auto_now`, the trigger fires unconditionally - `save(update_fields=["name"])` still
advances `updated_at` even though `"updated_at"` isn't in `update_fields`. Naming it explicitly
there is harmless leftover habit from `auto_now`, not something this still requires.

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
  auto-append readonly/list-display fields. `as_queryset()` is the row as a one-row queryset.
- `__str__` falls back to `REPR` (`"{self.__class__.__name__}(id={self.pk})"`) unless the
  subclass sets `STR` to its own format string.
- Don't put a `classproperty` with a query-building body on a subclass - use a plain `classmethod`
  instead. `django_lifecycle`'s `LifecycleModelMixin` scans class attributes on every
  instantiation to find hook methods, which evaluates a `classproperty` eagerly as a side effect;
  if that property builds a queryset by instantiating the same model, this recurses infinitely.
  This is a `django_lifecycle` behavior, not something `FullCleanOnSaveModel` can fix - just a trap worth
  knowing about.

## VersionedModel

`version`, a `PositiveBigIntegerField` the database keeps: `db_default=1` on insert, and a `BEFORE
UPDATE` trigger (`bump_version`) setting it to `OLD.version + 1` on every UPDATE, whatever the write
set it to - `QuerySet.update(version=99)` still moves it on by one. Not editable, so a `ModelSerializer`
or a form makes it read-only. A model with it uses `version` as its [concurrency token](concurrency.md),
over `updated_at`. Like `updated_at`, it doesn't count as a change in [history](../../../drf/serializers/history.md).

```python
from isik.django.apps.common.db import BaseModel, VersionedModel

class Document(VersionedModel, BaseModel):
    body = models.TextField()
```

Django doesn't read back what the trigger set, so `instance.version` after a `save()` is the value
before it until `refresh_from_db()`. `PreconditionMixin` reads it back for its responses.
