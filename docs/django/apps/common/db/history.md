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
