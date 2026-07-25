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
