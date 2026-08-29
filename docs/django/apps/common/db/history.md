# history

`track_events(**kwargs)` is a class decorator around `pghistory.track()`, pre-configured with
insert/update/delete event trackers so a model's history table always records all three without
repeating that setup at each call site. Extra `kwargs` pass straight through to
`pghistory.track()`.

```python
from isik.django.apps.common.db import track_events

@track_events()
class Widget(BaseModel):
    name = models.CharField(max_length=100)
    count = models.IntegerField(default=0)
```

- Generates a `WidgetEvent` history model with a `pgh_label` (`"insert"`/`"update"`/`"delete"`)
  and `pgh_obj`/`pgh_obj_id` columns, populated by real Postgres triggers on `Widget` — requires
  `pghistory` and a Postgres backend; nothing fires without a real database.

`event_model_for(model)` returns that generated Event model - `event_model_for(Widget) is
WidgetEvent` - raising `ImproperlyConfigured` if `model` was never tracked. `isik.django.drf`'s
`generic_history_serializer()`/`HistoryMixin` (see
[drf/serializers/history.md](../../../drf/serializers/history.md)/
[drf/viewsets/history.md](../../../drf/viewsets/history.md)) are built on top of it.

`history_middleware_installed()` is `True` if `pghistory.middleware.HistoryMiddleware` (or a
subclass) is in `settings.MIDDLEWARE` - that's what stamps `user`/`url` into pghistory's context,
so tracked events carry an actor. Used by `HistoryMixin` to add actor filtering/serialization only
when it'll actually have data.

## Real, indexed columns from context - `ContextField`

pghistory's own `pgh_context` is JSON, joined off a shared table, and unindexed by default - fine
for the occasional `/history/` lookup, too slow as a real, frequent filter (e.g. "everything this
actor created"). `ContextField` stamps a genuine, indexable column on the event model instead,
from the same request-scoped context `pghistory.context()`/`HistoryMiddleware` already populate -
no separate middleware needed.

```python
from isik.django.apps.common.db import ContextField, track_events

@track_events(
    context_fields=[
        ContextField("actor", context_key="user", cast="uuid", field=models.ForeignKey(
            settings.AUTH_USER_MODEL, null=True, on_delete=models.DO_NOTHING, db_constraint=False,
        )),
    ],
    # Composes for free - meta= and context_fields= both feed the same pghistory.track() call.
    meta={"indexes": [models.Index(fields=["actor"], name="widget_event_actor_idx")]},
)
class Widget(BaseModel):
    ...
```

- `name` is the field's attribute name on the event model, same as any other field - indexing
  needs nothing isik-specific: `db_index=True` on the field, or a `Meta.indexes` entry above.
- `context_key` is the key read out of `pghistory.context(**metadata)` - defaults to `name`.
- `cast` is the Postgres type the extracted value is cast to; inferred for UUID/int/bigint/text/
  bool, required for `ForeignKey` (the related model may not be loaded yet when this runs).
- `on_delete=models.DO_NOTHING, db_constraint=False` mirrors pghistory's own default for
  `pgh_obj`/`pgh_context` - an event row is a historical record and shouldn't vanish or block a
  delete just because the actor/tenant it once pointed to is gone.
- Outside any `pghistory.context()`/`HistoryMiddleware` block (a management command, a Celery
  task with no context set), the column is just `NULL` - give it `null=True`.
