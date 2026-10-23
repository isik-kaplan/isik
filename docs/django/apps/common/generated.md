# generated

The models isik generates are marked, so a project needn't guess them by their names:

| Kind | Made by |
|---|---|
| `"event"` | `track_events()` - a tracked model's event model |
| `"maker"` | a model maker - `tags()`, `votes()`, `comments()`, `notes()`, `bookmarks()` |
| `"lock"` | a version's `concurrency_relations` - the proxy its lock trigger is installed on |

```python
from isik.django.apps.common.generated import generated_models

Widget.pgh_event_model.isik_generated   # "event"
generated_models("event")               # every event model isik made, in the order made
generated_models()                      # all of them
```

`mark_generated(model, kind)` marks one and returns it. [`enum_name_overrides()`](../../drf/spectacular.md)
names the generated models' enums from the registry, and the [`documentation` check](checks.md) skips
them - their fields were written by isik.
