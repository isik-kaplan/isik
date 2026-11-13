# choices

`isik.django.apps.common.db.choices` holds a field's choices to how they change over the life of a row:

- **retired values** - out of the field's choices, kept by a row already holding one, and never written anew;
- **transitions** - the moves an update makes from one value to another.

Both hold in Python, through `full_clean()`, and at the column, through a trigger - on every write, the ORM's,
`QuerySet.update()`'s, `bulk_create`'s, a migration's and raw SQL's.

```python
from isik.django.apps.common.db import BaseModel, EnumField, with_choice_rules


class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW = "review", "In review"
    PUBLISHED = "published", "Published"


class Article(BaseModel):
    status = EnumField(
        Status,
        default=Status.DRAFT,
        retired=("legacy",),
        transitions={
            Status.DRAFT: {Status.REVIEW},
            Status.REVIEW: {Status.PUBLISHED, Status.DRAFT},
            "legacy": {Status.PUBLISHED},
        },
    )
    priority = with_choice_rules(
        models.IntegerField(choices=[(1, "Low"), (2, "High")], null=True),
        transitions={None: {1}, 1: {2}, 2: {1, None}},
    )
```

## EnumField

`EnumField(enum, *, retired=(), transitions=None, **kwargs)` is a `TextChoices` or `IntegerChoices` enum's field:

- a `TextChoices` enum gives a `CharField` as long as its longest value - `max_length=` sets another length;
- an `IntegerChoices` enum gives an `IntegerField`;
- the enum's choices are the field's, and `kwargs` are the field's own (`default`, `null`, `help_text`, ...);
- `retired` and `transitions` are `with_choice_rules()`'s.

It returns the plain field, so its migration is a `CharField`'s or an `IntegerField`'s. A longer value added to
the enum moves `max_length` with it, in the migration adding the choice.

## with_choice_rules

`with_choice_rules(field, *, retired=(), transitions=None)` gives a field with choices its rules, and returns the
field - any field: a `CharField`, an `IntegerField`, one a library declares, rebuilt with `redeclare()`:

```python
status = with_choice_rules(redeclare(LibraryBase.status, help_text="Where the row stands."), retired=("old",))
```

It takes `retired`, `transitions` or both. Values are read by the field, so an enum's members, plain values and
a number given as a string all name the same choice.

### retired

The values a row already holding one keeps, out of the field's choices:

- the field's CHECK admits them beside its choices, so `migrate` keeps the rows holding them;
- an insert never takes one, and an update never moves a row to one;
- a row holding one saves with any other change, and moves off it where `transitions` lists the move;
- forms, serializers and the schema offer the field's choices - the retired values are out of them.

Retiring a value is moving it from the choices to `retired`. `makemigrations` writes the CHECK and the trigger
in one migration. Dropping it from `retired` too, once no row holds it, narrows the CHECK in the next.

### transitions

`{value: values it moves to}`. An update changing the field moves it along one of these, and:

- a value left out of the keys moves nowhere - a final state;
- an insert takes any of the field's choices;
- a nullable field names null as `None`, on either side: `{None: {1}}` sets a value on a row holding none,
  `{2: {None}}` clears it;
- with `transitions` unset, any value moves to any.

### What it refuses as you write it

`with_choice_rules()` raises `TypeError` for:

- a field with no choices;
- neither `retired` nor `transitions`;
- a string as `retired` - it is a tuple of values;
- `None` in `retired` - null is the column's own;
- a retired value still among the field's choices;
- a value in `transitions` the field neither offers nor retires (or `None` on a field that isn't nullable);
- a move to a retired value.

## Where it holds

On a managed, concrete `FullCleanOnSaveModel` - `BaseModel` is one. `Check.CHOICE_RULES` (`isik.E026`) refuses
choice rules on any other model.

### In Python

`full_clean()`, which `FullCleanOnSaveModel` runs on every save, refuses:

| refused | message | code |
|---|---|---|
| a retired value written | `legacy is a retired status - only a row already holding it keeps it.` | `retired_choice` |
| a move `transitions` leaves out | `status moves from draft to review, never to published.` | `invalid_transition` |

isik's `exception_handler` turns either into a 400.

A move is held against what the row holds as the instance last read or wrote it: loaded, refreshed, or saved
with the field among the columns written. Two saves in one transaction move it twice -
`draft → review`, then `review → published` - each against the one before. An instance never loaded from its
row, or with the field deferred and unread, is held by the column alone.

### At the column

The trigger `<field>_choice_rules`, before an insert or an update changing the field, refuses the same, with
SQLSTATE `23514` (`check_violation`) - an `IntegrityError` in Django:

- `'legacy' is a retired status - only a row already holding it keeps it.`
- `status never moves from 'draft' to 'published'.`

Every write to the table meets it: `QuerySet.update()`, `bulk_create`, a data migration, `psql`. A fix that has
to write a retired value goes through `pgtrigger.ignore("app.Model:status_choice_rules")`, as with any other of
isik's triggers.

The trigger holds its values itself, so a migration renders it from the rules as they were when it was written.
