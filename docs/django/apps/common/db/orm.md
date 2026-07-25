# orm

Two small queryset/ORM helpers that don't fit the `models.py`/`history.py` split.

## starts_with

`starts_with(field, prefix)` returns a `Case`/`When` expression annotating `True`/`False` for
whether `field` starts with `prefix` — use it in `.annotate()`, not as a filter directly.

```python
from isik.django.apps.common.db.orm import starts_with

Widget.objects.annotate(is_bolt=starts_with("name", "bolt"))
```

## get_object_or_none

Like `Model.objects.get(**kwargs)`, but returns `None` instead of raising — both on
`Model.DoesNotExist` and on a `ValidationError` from an invalid lookup value (e.g. a malformed
UUID string against a UUID primary key).

```python
from isik.django.apps.common.db.orm import get_object_or_none

widget = get_object_or_none(Widget, pk=maybe_invalid_id)
```
