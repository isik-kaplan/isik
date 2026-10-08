# constraints

`as_condition`, `no_database_form`, `python_only_validator` — a validator says whether its rule can
hold at the column, and the ones that can become `CheckConstraint`s. `lifted_constraint` lifts one
for a test.

## The problem

A validator runs in `full_clean()` and nowhere else. These all write straight past it:

```python
Thing.objects.filter(pk=pk).update(status="nonsense")   # no full_clean
Thing.objects.bulk_create([...])                        # no full_clean
# a data migration, a psql session, a fix applied by hand at three in the morning
```

So a rule kept only in a validator is a rule the database does not have. The same goes for
`choices`, which Django checks in `full_clean()` and nowhere else — a `status` column takes any
string that fits in it.

## Three answers, and no fourth

Every validator a model declares must say which it is. One that says nothing fails `isik.E001`,
because the validator nobody thought about is the one that should error.

### `as_condition` — it knows its own SQL

```python
@as_condition(lambda field, validator: Q(**{f"{field.name}__gte": 0}))
def positive_only(value):
    if value < 0:
        raise ValidationError("Must be positive.")
```

A `CheckConstraint` is generated from it. Returning `None` says **the column carries the rule
already**, which is how the built-ins avoid repeating themselves:

| validator | generates | because |
| --- | --- | --- |
| `MinLengthValidator` | a constraint | nothing about `varchar(n)` enforces a minimum |
| `MaxLengthValidator` | nothing on a `varchar` | the type refuses a longer value by itself |
| `MinValueValidator(0)` on a `PositiveIntegerField` | nothing | Django writes that CHECK itself |
| `MaxValueValidator(2147483647)` there | nothing | that is the `integer` type's own ceiling |

A `TextField` with `max_length` is `text`, which carries nothing — so there it does generate one.

### `no_database_form` — no instance of it ever could

For a rule that reaches outside the row: a remote lookup, a clock, a file.

```python
@no_database_form("It resolves the host now, which a column cannot do and would not agree with later.")
def refuse_an_unreachable_host(value): ...
```

The reason is an [`Exemption`](../../../../common/utils/exemptions.md), so it is held to a floor and
listed by `manage.py exemptions` beside everything else in the project that skips a rule.

### `python_only_validator` — expressible, declined here

Per instance rather than per class, because the same validator can be worth holding at one column
and not at another:

```python
count = models.IntegerField(validators=[python_only_validator(
    positive_only,
    reason="SkipFieldValidators lifts this one, and a CHECK constraint cannot be lifted.",
)])
```

## A constraint cannot be skipped, and that is the decision this makes for you

This is the trade, and it is worth reading before enforcing anything:

- `SKIP_FULL_CLEAN`, `skip_full_clean()`, `SkipFieldValidators` and `SkipNamedValidators` lift a
  validator. They cannot lift a `CheckConstraint`.
- Postgres offers no escape either. `DEFERRABLE` is **rejected** on a CHECK — only `UNIQUE`,
  `FOREIGN KEY` and `EXCLUDE` may be deferred — and `NOT VALID` only forgives rows that already
  exist, never a later write.

So a rule you need to be able to lift belongs in `python_only_validator`, with the reason saying so.
Enforcing it at the column takes the escape hatch away, and does it silently.

## What else it writes

A field with `choices` gets `IN (...)`, admitting null where the column is nullable and the empty
string where it is `blank` — refusing either would refuse what the field was declared to allow.

### A callable `choices` is a snapshot

`choices=language_choices`, built from `settings.LANGUAGES`, is evaluated once, when the app starts,
into both `Meta.constraints` and the migration. The CHECK holds the list as it was then:

- Changing the setting in production does not change the column. The two disagree until someone runs
  `makemigrations`, and the migration it writes is the change.
- Changing it in a test (`override_settings`, a fixture) does not change the constraint either.
  `full_clean()` validates it in Python against the startup list, and the column refuses the value
  too.

To test a value the project's own settings don't allow, lift that one CHECK for the block:

```python
from isik.django.apps.common.db.constraints import lifted_constraint


@override_settings(LANGUAGES=[("en", "English"), ("tr", "Turkish")])
def test_a_second_language(db):
    with lifted_constraint(User, "users_user_language_choices"):
        User.objects.create(username="ayse", language="tr")
```

It lifts the constraint from `_meta.constraints`, which `full_clean()` validates against, and drops it
from the table. Dropping it is DDL, so it runs only inside a transaction that rolls back, the test's
own, and is refused outside one. Afterwards the Python list is put back as it was, and the column gets
the constraint back `NOT VALID`: later writes are checked again, while the rows the block wrote stay
until the rollback. A block that raises gets it back in Python only. Its transaction is likely broken,
and the rollback restores the column.

## Notes

- Constraints are added from `AppConfig.ready()` **and** from `class_prepared`. Neither covers the
  other: a model building its own columns from a `class_prepared` receiver is raced by a second
  receiver, and a test using `isolate_apps` prepares models long after `ready()`. The work is
  idempotent.
- Names carry the table, because Django wants them unique across models rather than per table, and
  are hashed past 63 characters — Postgres truncates an identifier rather than refusing it, so two
  names differing only in their tail would silently become one constraint.
- A `CHECK` evaluating to `NULL` is satisfied, so a nullable column needs no special arm.
- The check reads `field._validators` — what a model actually passed. Django appends its own from
  `max_length` and from a field's range, and nobody can wrap what they did not write.
