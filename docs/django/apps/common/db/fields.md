# fields

`redeclare(field, **changes)` - the field an abstract model declares, built again from its own definition
with `changes` made, to declare on a model inheriting it:

```python
from django.contrib.auth.models import AbstractUser
from isik.django.apps.common.db import if_unset, redeclare


class User(AbstractUser):
    first_name = redeclare(
        AbstractUser.first_name,
        max_length=200,
        help_text="What the person is called first.",
        db_comment="What the person is called first.",
    )
    is_staff = redeclare(
        AbstractUser.is_staff,
        help_text=if_unset("Whether the person can sign in to the admin."),
        db_comment="Whether the person can sign in to the admin.",
    )
```

- **The library's definition, kept.** It is read off the field's own `deconstruct()`, so its `max_length`,
  `unique`, `default`, choices and validators stay what the library made them - nothing is copied by hand,
  and nothing drifts from it. What changes is what `changes` names, any argument the field's constructor
  takes; one it doesn't take fails where it's written, at import.
- **A plain value sets it; `if_unset(value)` fills it.** `if_unset` is used only where the library left that
  argument at Django's default: Django's own `help_text` on `is_staff` stays, and the `db_comment` it never
  wrote is filled.
- **Read off the model it's inherited through.** `AbstractUser.password` is the field `AbstractBaseUser`
  declares, `AbstractUser.is_superuser` the one `PermissionsMixin` does - the field itself (`.field`) is
  taken too. A primary key, a many-to-many, a key to a swappable model are rebuilt like any other - a key to
  the user model in a models module, before the app registry is ready, included - and a key declared
  `swappable=False` stays so.
- **The project's own.** Declared again, it is checked by `isik.E010` like a field the project wrote -
  with fields inherited from libraries checked or not ([checks](../checks.md)) - and its migration holds it
  as the model's.

Refused, with `ImproperlyConfigured`: a concrete model's field - its column is in that model's own table,
which no model inheriting it declares - and a call changing nothing.
