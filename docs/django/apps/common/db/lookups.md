# lookups

Registers a `length` lookup (backed by Django's `Length` function) on `CharField` and `TextField`
at import time, so field length becomes a normal queryset lookup instead of requiring an explicit
`.annotate(Length(...))`. Imported for its side effect only — `CommonConfig.ready()` (in
`apps.py`) imports this module so the registration happens once, at app startup.

```python
Widget.objects.filter(name__length=4)
Widget.objects.filter(name__length__gt=4)
```

- Nothing to import directly from this module — just needs `isik.django.apps.common` in
  `INSTALLED_APPS` so `ready()` runs.
