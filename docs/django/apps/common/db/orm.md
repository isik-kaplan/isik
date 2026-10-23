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

## with_python_defaults

An unsaved instance holds a placeholder (`DatabaseDefault`) in each field the database defaults
(`db_default`) until the row is written - which code encoding the unsaved instance can't encode: a
library serializing a user it hasn't saved yet, say, or `BaseModel`'s `created_at`.
`with_python_defaults(instance)` gives each such field its value in Python instead, and returns the
instance: `Now()` becomes `timezone.now()`, a literal its value. Any other expression raises
`ValueError` naming the field.

```python
from isik.django.apps.common.db import with_python_defaults

user = with_python_defaults(User(email=email))
json.dumps(model_to_dict(user), cls=DjangoJSONEncoder)
```

The row, once saved, holds the database's own values - `refresh_from_db()` reads them.
