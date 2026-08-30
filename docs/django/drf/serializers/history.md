# history

`generic_history_serializer(model)` builds a read-only `Serializer` for the history of a model
tracked with [`@track_events()`](../../apps/common/db/history.md), instead of hand-writing one per
model.

```python
from isik.django.drf.serializers import generic_history_serializer

WidgetHistorySerializer = generic_history_serializer(Widget)
WidgetHistorySerializer(some_queryset, many=True).data
```

- `event_id`/`event_created_at`/`action` (`"insert"`/`"update"`/`"delete"`) are the history
  record's own identity - renamed from pghistory's `pgh_id`/`pgh_created_at`/`pgh_label` so they
  read as an API, not raw pghistory column names.
- `changes` is a dict of `{field: [old, new]}` for whatever changed since the previous event of
  the same object - `None` on the first (`"insert"`) event. Computed in SQL by
  `pghistory.models.Events`, not recomputed here - except that any
  [`ContextField`](../../apps/common/db/history.md#real-indexed-columns-from-context---contextfield)
  column is filtered back out: pghistory's SQL diffs every non-`pgh_`-prefixed column generically,
  so a context field would otherwise appear as a "change" whenever the acting context differs from
  the previous event (e.g. `{"actor_id": [alice.pk, bob.pk]}`) even though nothing about the
  tracked object itself changed.
- Every tracked field is flattened at the top level under its own name (`name`, `count`, …),
  typed to match the real model field. A foreign key surfaces as `<field>_id` - the raw stored id,
  not a hydrated relation, since this reads from a JSON snapshot rather than a live queryset.
- `actor_id` is added too, if `pghistory.middleware.HistoryMiddleware` (or a subclass) is in
  `settings.MIDDLEWARE` - see `history_middleware_installed()`.
- Raises `ImproperlyConfigured` if a tracked field is itself named `event_id`/`event_created_at`/
  `action`/`changes`/`actor_id`, rather than one silently clobbering the other - rename the field
  or exclude it from tracking (`track_events(exclude=[...])`). Exception: an `actor_id` produced
  by a [`ContextField`](../../apps/common/db/history.md#real-indexed-columns-from-context---contextfield)
  isn't a collision - it's the same fact the JSON-derived `actor_id` above would otherwise stand
  in for, just as a real, typed column, so it wins instead of raising.

## Withholding a field - `withhold=`

`generic_history_serializer(model, withhold=["password"])` keeps a tracked field out of the
flattened output entirely, while `changes` still records that it changed at that event - just with
its `[old, new]` pair replaced by `[None, None]` instead of the real values. This is a different
question from `track_events(exclude=[...])`, which drops a field from the event table itself:
whether a value is in the log is retention, whether an API renders it is exposure, and a field can
reasonably want yes to the first and no to the second - a password hash is worth knowing changed,
never worth serving. `withhold` is one name at a time, explicit - isik never guesses at what
"looks sensitive"; only the consuming project knows which of its own fields that is.

```python
generic_history_serializer(User, withhold=["password"])
# {"event_id": 4, "action": "update", "changes": {"password": [None, None]}, ...}
# ("password" itself is absent from the flattened output, not merely null)
```

See [`HistoryMixin`](../viewsets/history.md) for exposing this over a viewset action.
