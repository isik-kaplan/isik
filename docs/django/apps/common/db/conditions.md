# conditions

`isik.django.apps.common.db.conditions` - trigger conditions that leave a model's database-kept
fields out: `version` on a [`VersionedModel`](models.md#versionedmodel), `updated_at` on a
`DatabaseTimestampsModel` (so every `BaseModel`).

isik's own triggers move those fields whenever a write changes the row
([a write that changes nothing](models.md#a-write-that-changes-nothing)). A condition comparing them
sees every change as a change to them:

- **After the write.** An AFTER trigger's condition reads the row as written, kept fields moved.
  `AnyChange(exclude=["last_seen"])` is meant to skip a write to `last_seen` alone, and fires for it
  anyway: `updated_at` moved with it.
- **Before the write.** Postgres runs BEFORE triggers in name order. One sorting before `bump_version`
  or `stamp_updated_at` sees the fields as the write sent them, not yet kept: an instance saved twice
  sends its first save's stale `updated_at`, which reads as a change.

Comparing every column in an AFTER trigger is fine: the kept fields move only when another column
changed, so the condition is true exactly when the row changed. That is pghistory's own default update
condition, `AnyChange()`.

## AnyChange, AnyDontChange, AllChange, AllDontChange

pgtrigger's (and pghistory's) four change conditions, taking the same arguments, with the model's kept
fields added to `exclude` when they're resolved. A drop-in for either library's:

```python
from isik.django.apps.common.db import track_events
from isik.django.apps.common.db.conditions import AnyChange


@track_events(condition=AnyChange(exclude=["last_seen"]))
class Device(BaseModel):
    name = models.CharField(max_length=100)
    last_seen = models.DateTimeField(null=True)
```

Naming a kept field among the fields to compare (`AnyChange("name", "updated_at")`) raises `ValueError`
when the trigger is rendered: the condition would leave out the field it was asked about. The kept
fields are read from the model as registered, by its label, so a migration's model of its own state
resolves them too.

## kept_field_condition_problems(app_configs)

Each trigger on a model of `app_configs` whose condition compares a kept field, one sentence each, or
`[]`. It reads pgtrigger's registry, so pghistory's event triggers are included, and it reads:

- a change condition (`AnyChange` and the rest) - by the fields it compares, after `exclude` and
  `exclude_auto`. Every column in an AFTER trigger is fine; every column in a BEFORE trigger isn't.
- a `pgtrigger.Q` - by the `old__<field>`/`new__<field>` it names, in its keys and in `F()` values,
  nested `Q`s included.

A condition written as SQL (`pgtrigger.Condition("...")`) can't be read, and passes.

The system check `isik.E013` (`Check.KEPT_FIELD_CONDITIONS`) reports them, over the project's own apps
by default - `CheckOption.APPS` names others ([checks](../checks.md)).
