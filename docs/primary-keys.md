# Primary keys

Every primary key isik has is a UUID called `id`, made by `new_primary_key()`. That covers isik's own
models, every model it makes for a project (`votes()`, `tags()`, a concurrency lock), every abstract base
a project subclasses, and the test projects' models. Like [naming](naming.md), it's written down so it's
held rather than followed by habit. `tests/test_primary_keys.py` fails on a model that breaks it.

1. **A UUID called `id`, with `new_primary_key` as its default.** Inherit `UUIDPrimaryKeyModel` (or
   `BaseModel`), or declare the field the same way. Never an `AutoField`, never a natural key: a row
   with a name (a feature, a kind of notice) keys it by `id` and keeps the name `unique`. A model joined
   to another one-to-one has its own `id` beside that key, not the other row's.
2. **`new_primary_key()` makes a uuid7 unless `ISIK_PRIMARY_KEY` says otherwise.** `ISIK_PRIMARY_KEY` is
   a dotted path to a function returning a UUID, and isik's `uuid7` is the default. A migration names
   `new_primary_key`, not the function, so changing the setting needs no migration.
3. **A key to such a row stores its UUID.** Code with a row's name looks it up by a join
   (`kind__name=...`), or by a subquery inside the INSERT
   (`kind_id=Subquery(Policy.objects.filter(name=...).values("pk"))`), not with a query of its own. A
   listing that shows the name loads the row with `select_related`. A row mirrored from a declaration (a
   feature, a kind) gets its key in each database it's mirrored into, so what leaves the database - an
   export, an archive, a queued message - carries its name, never its key.
4. **A row written in SQL gets `DatabaseUUID7()`** as its `db_default`, since no Python function reaches a
   trigger. The notice is the one such row today. Its Python default is still `new_primary_key`, so a
   notice written from Python follows the setting.

## What the rule doesn't reach

Keys isik can't choose:

- Django's own tables (`contenttypes`, `auth`, `sessions`, `admin`) and pghistory's `Context`.
- A history event's `pgh_id`. pghistory numbers events with a sequence, and an event's previous version is
  found by `pgh_id < N`. A UUID made in Python or by PostgreSQL before 18 can't promise that order
  between two events in one millisecond.
- A project's own models, and the many-to-many tables Django makes for them. isik works with whatever key
  a project chose. The test projects keep a few models keyed by integers for that reason, each named in
  `tests/test_primary_keys.py` with why.
