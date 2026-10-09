# history

`generic_history_serializer(model, serializer)` builds a read-only `Serializer` for the history of a
model tracked with [`@track_events()`](../../apps/common/db/history.md), showing what `serializer` -
the resource's own serializer - shows, and nothing else.

```python
from isik.django.drf.serializers import generic_history_serializer

WidgetHistorySerializer = generic_history_serializer(Widget, WidgetSerializer)
WidgetHistorySerializer(some_queryset, many=True).data
# [{"event_id": 12, "event_created_at": "...", "action": "update",
#   "changes": {"count": [0, 5]}, "id": "...", "name": "New name", "count": 5}, ...]
```

History is the resource over time, so it never shows a column the resource doesn't, and there is
no second list to keep in step with the serializer: leave a field out of the serializer and it's
out of the history too.

- `event_id`/`event_created_at`/`action` (`"insert"`/`"update"`/`"delete"`) are the history
  record's own identity - renamed from pghistory's `pgh_id`/`pgh_created_at`/`pgh_label` so they
  read as an API, not raw pghistory column names.
- Then every field of `serializer` that renders one tracked column, under the serializer's name for
  it and rendered by the serializer's own field - so an entry reads like the resource did at that
  moment, same names, same formats, same schema types. That covers a plain column (`name`, or
  `title` with `source="name"`) and a relation as its primary key (`PrimaryKeyRelatedField`, or an
  `owner_id` field).
- A method field, a nested serializer, a dotted source (`owner.name`), `source="*"`, a slug or other
  related field that isn't a primary key, and a write-only field are left out. Each would read
  today's related rows or computed state and present it as history.
- `changes` is `{name: [old, new]}` over the same fields, for whatever changed since the previous
  event of the same object - `None` on the first (`"insert"`) event, and when nothing shown changed.
  Computed in SQL by `pghistory.models.Events`; each value is rendered by the same field as above.
- A [`ContextField`](../../apps/common/db/history.md#real-indexed-columns-from-context---contextfield)
  column is shown under its own name - it records who acted, not a field of the object - and never
  appears in `changes`.
- `actor_id` is added too, if `pghistory.middleware.HistoryMiddleware` (or a subclass) is in
  `settings.MIDDLEWARE` - see `history_middleware_installed()` - unless an `actor` `ContextField`
  already provides it as a real, typed column.
- The class carries `history_columns`, the tracked columns whose change shows in `changes`;
  [`HistoryMixin`](../viewsets/history.md) leaves out an update that changed none of them.
- Raises `ImproperlyConfigured` if a shown field is named `event_id`/`event_created_at`/`action`/
  `changes`/`actor_id` or after a `ContextField`, rather than one silently clobbering the other -
  rename it in the serializer, or give the history a serializer of its own.

## Showing that a hidden field changed - `shows_change_of=`

`shows_change_of` names tracked model fields the resource hides whose changes should still appear,
as `[None, None]` - present, so the event says the field changed and when, with neither value in it:

```python
generic_history_serializer(User, UserSerializer, shows_change_of=["password"])
# {"event_id": 4, "action": "update", "changes": {"password": [None, None]}, ...}
# ("password" itself is never in the entry)
```

A name that isn't a tracked field, or that the serializer already shows, raises
`ImproperlyConfigured`. Leaving a field out of the history altogether is a different question from
`track_events(exclude=[...])`, which drops it from the event table itself: whether a value is in the
log is retention, whether an API renders it is exposure.

See [`HistoryMixin`](../viewsets/history.md) for exposing this over a viewset action.

`generic_history_serializer(model, serializer, name=...)` overrides the generated class name
(default `<Model>HistorySerializer`).
