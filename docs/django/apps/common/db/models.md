# models

`BaseModel` is four abstract models composed together, each usable alone:

| Model | Adds |
|---|---|
| `UUIDPrimaryKeyModel` | a UUID primary key - `uuid4` by default, see [primary keys](#primary-keys) |
| `DatabaseTimestampsModel` | `created_at`/`updated_at` kept by the database (needs `pgtrigger`) |
| `FullCleanOnSaveModel` | `full_clean()` and `django-lifecycle` hooks on every `save()`, skippable validators, `update()`, `skip_full_clean()` |
| `ReprModel` | `STR`/`REPR` format strings for `str()`/`repr()` |

Not part of `BaseModel`, and added beside it when wanted:

| Model | Adds |
|---|---|
| `VersionedModel` | a `version` the database counts up whenever the row changes (needs `pgtrigger`) |

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
by a `BEFORE UPDATE` trigger whenever the row changes, including through `QuerySet.update()`/`bulk_update()`/raw
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

Unlike `auto_now`, `update_fields` doesn't gate it - `save(update_fields=["name"])` advances
`updated_at` when `name` changed, even though `"updated_at"` isn't in `update_fields`. Naming it
explicitly there is harmless leftover habit from `auto_now`, not something this still requires.

### A write that changes nothing

The trigger compares every other column with the row as it was. A write changing none of them -
`save()` on an unedited instance, `update(name=...)` to the name it has - leaves `updated_at` (and
`version`) where they were. So the ETag stays put, and a history event's condition sees no change and
records nothing. The trigger still fires on that write, holding the column at what the row had: Django
writes every field back, so an instance saved twice sends the first save's stale `updated_at`, and
without that the token would go backwards.

`database_kept_fields(model)` names the fields the database keeps this way: `("version", "updated_at")`
on a model with both, `()` on one with neither.

A change the row doesn't show - rows of another table that the row's version should cover, say -
moves them with `touch_rows(queryset)`, which writes the first kept field one step forward. A kept
field written forward counts as a change, so every kept field moves with it:

```python
from isik.django.apps.common.db import touch_rows

touch_rows(Invoice.objects.filter(pk=invoice.pk))   # returns the number of rows, as update() does
```

A trigger of the project's own whose condition compares columns should leave the kept fields out - see
[conditions](conditions.md).

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

## Primary keys

`UUIDPrimaryKeyModel.id` defaults to `new_primary_key()`, which calls the function the
`ISIK_PRIMARY_KEY_DEFAULT` setting names - `"uuid.uuid4"` when it's unset. isik ships `uuid7()`, a
time-ordered UUID (RFC 9562: 48 bits of Unix milliseconds, then random bits), whose new rows land at
the end of the primary key's index rather than all over it:

```python
ISIK_PRIMARY_KEY_DEFAULT = "isik.django.apps.common.db.models.uuid7"
```

**A project with a key convention of its own sets it.** Nothing fails when it doesn't: every model
isik keys - its own apps' among them - quietly gets random `uuid4` keys beside the project's, the wrong
shape for good on exactly the append-heavy tables read by recency, where a key that doesn't sort by time
spreads every insert across the index.

The setting is read once per process. A model's migration names `new_primary_key`, not the setting,
so changing it needs no migration - a project upgrading from 0.29 gets one `AlterField` per model,
from `uuid.uuid4` to `new_primary_key`.

## VersionedModel

`version`, a `PositiveBigIntegerField` the database keeps: `db_default=1` on insert, and a `BEFORE
UPDATE` trigger (`bump_version`) setting it to `OLD.version + 1` whenever the row changes, whatever the
write set it to - `QuerySet.update(name="nut", version=99)` still moves it on by one, and a write
changing nothing else leaves it where it was ([a write that changes nothing](#a-write-that-changes-nothing)). Not editable, so a `ModelSerializer`
or a form makes it read-only. A model with it uses `version` as its [concurrency token](concurrency.md),
over `updated_at`. Like `updated_at`, it doesn't count as a change in [history](../../../drf/serializers/history.md).

```python
from isik.django.apps.common.db import BaseModel, VersionedModel

class Document(VersionedModel, BaseModel):
    body = models.TextField()
```

Django doesn't read back what the trigger set, so `instance.version` after a `save()` is the value
before it until `refresh_from_db()`. `PreconditionMixin` reads it back for its responses.
