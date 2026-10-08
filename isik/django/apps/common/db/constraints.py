"""A validator says whether its rule can hold at the column, and the ones that can become CHECKs.

A validator runs in `full_clean()` and nowhere else. A `QuerySet.update()`, a `bulk_create`, a data
migration and a psql session all write straight past it, so a rule kept only there is a rule the
database does not have. Where it is expressible in SQL it should also be a `CheckConstraint`, and
this decides which rules those are.

Three answers, and no fourth:

* **`as_condition(field, validator)`** on the validator's class - it knows its own SQL, and the
  constraint is generated. Returning `None` says the column already carries the rule: a `varchar(n)`
  refuses a longer value by itself, and `PositiveIntegerField`'s bounds are the `integer` type plus a
  CHECK Django writes itself.
* **`@no_database_form(reason)`** on the class - no instance of it could ever be a constraint. A
  remote lookup, a rule that reads the clock, anything outside the row.
* **`python_only_validator(inner, reason=...)`** around one instance - the rule is expressible and
  this use declines it, which is also how a validator stays skippable: a CHECK cannot be bypassed,
  so a rule that `SkipFieldValidators` is meant to lift belongs here.

Anything else fails `isik.E001`. The validator nobody thought about is the one that errors, which is
the direction to be wrong in.
"""

import hashlib
import inspect

from django.core import validators as django_validators
from django.db import connection, models
from django.db.models.signals import class_prepared
from django.utils.deconstruct import deconstructible

from isik.common.utils.exemptions import Exemption
from isik.django.apps.common.db.models import FullCleanOnSaveModel


# Postgres truncates an identifier at 63 characters rather than refusing it, so a name that overruns
# collides with its neighbor and two constraints silently become one.
IDENTIFIER_LIMIT = 63


class NoDatabaseForm(
    Exemption,
    rule="isik.validators.database-form",
    why="A validator runs in full_clean() alone, so a rule only it holds is one a direct write ignores.",
):
    """This rule cannot hold at a column, and here is why."""


def as_condition(build):
    """Give a validator of your own its SQL form.

        @as_condition(lambda field, validator: Q(**{f"{field.name}__gte": 0}))
        def positive_only(value): ...

    `build` is handed the field and the validator and returns a `Q`, or None where the column
    carries the rule already.
    """

    def mark(validator):
        validator.as_condition = build
        return validator

    return mark


def no_database_form(reason):
    """Mark a validator class, or a plain function, as one no column could hold.

    @no_database_form("It resolves the host now, which a column cannot do.")
    def refuse_an_unreachable_host(value): ...
    """

    def mark(validator):
        validator.no_database_form = NoDatabaseForm(reason=reason)
        return validator

    return mark


@deconstructible
class python_only_validator:  # noqa: N801 - it reads as a function at the call site, which is where it is used
    """One validator whose rule stays in Python, with why.

    For the instance rather than the class, because the same validator can be worth holding at one
    column and not at another - and because a `CheckConstraint` cannot be deferred or ignored, so a
    rule that has to stay liftable by `SkipFieldValidators` has to stay here.

    `deconstructible` with `__eq__`, or every `makemigrations` sees a new object and writes a
    migration for it.
    """

    def __init__(self, validator, *, reason):
        self.validator = validator
        # Under the name the class-level marker uses, so one lookup answers for both.
        self.no_database_form = NoDatabaseForm(reason=reason)

    @property
    def __name__(self):
        """The one it wraps. `make_skippable` reads this to name the validator, so without it
        `SkipNamedValidators` stops finding anything wrapped here."""
        return getattr(self.validator, "__name__", None) or type(self.validator).__name__

    def __call__(self, value):
        return self.validator(value)

    def __eq__(self, other):
        return isinstance(other, type(self)) and (self.validator, self.no_database_form) == (
            other.validator,
            other.no_database_form,
        )

    def __hash__(self):
        # Not the inner validator: Django's define `__eq__` without `__hash__`, so most are unhashable.
        return hash(self.no_database_form)


def _at_least_this_long(field, validator):
    return models.Q(**{f"{field.name}__length__gte": validator.limit_value})


def _at_most_this_long(field, validator):
    """Nothing where the column type carries it already: a `varchar(n)` refuses a longer value by
    itself, while a `TextField` with `max_length` is `text` and does not."""
    if field.db_type(connection).startswith("varchar"):
        return None
    return models.Q(**{f"{field.name}__length__lte": validator.limit_value})


def _within_the_columns_own_range(field, validator, end):
    """Nothing where the number is the column's own limit.

    Django adds a pair of these from the field type - `PositiveIntegerField` asks for 0 and
    2147483647 - and both ends are held already, the top by `integer` and the bottom by a CHECK
    Django writes itself. Repeating them puts noise in the schema and says nothing new.
    """
    bounds = connection.ops.integer_field_range(field.get_internal_type())
    if bounds and bounds[end] == validator.limit_value:
        return None
    return models.Q(**{f"{field.name}__{'gte' if end == 0 else 'lte'}": validator.limit_value})


# Django's own, told here rather than subclassed, so nothing has to be imported from isik to get it
# and the validators Django builds for itself are answered too.
DJANGO_VALIDATORS = {
    django_validators.MinLengthValidator: _at_least_this_long,
    django_validators.MaxLengthValidator: _at_most_this_long,
    django_validators.MinValueValidator: lambda field, v: _within_the_columns_own_range(field, v, 0),
    django_validators.MaxValueValidator: lambda field, v: _within_the_columns_own_range(field, v, 1),
}

DJANGO_VALIDATORS_WITHOUT_ONE = {
    django_validators.RegexValidator: (
        "Python's `re` and Postgres's POSIX operators are different dialects - lookahead and the "
        "unicode classes either fail to compile or match differently - so a generated pattern would "
        "disagree with the validator beside it. One worth holding is written as a constraint by hand."
    ),
    django_validators.EmailValidator: (
        "An address is that regex plus a domain-literal branch, and a CHECK disagreeing with it "
        "would refuse rows the application had already accepted."
    ),
    django_validators.URLValidator: "The same as the address above, over a longer pattern.",
    django_validators.DecimalValidator: "`numeric(m, d)` is the same rule, and the column has it already.",
    django_validators.ProhibitNullCharactersValidator: "Postgres refuses a null byte in text of its own accord.",
    django_validators.FileExtensionValidator: "What a file is named is not what the column holds.",
    django_validators.StepValueValidator: "No column declares a step, so a constraint would be checking nothing.",
}


def teach_django_its_validators():
    """Attach the answers to Django's classes, in place.

    In place rather than by subclass so that the instances Django has already built are answered,
    and so no second class exists for an `isinstance` elsewhere to disagree about.
    """
    for validator, build in DJANGO_VALIDATORS.items():
        validator.as_condition = staticmethod(build)
    for validator, why in DJANGO_VALIDATORS_WITHOUT_ONE.items():
        validator.no_database_form = NoDatabaseForm(reason=why)


def classified(validator):
    """Whether this validator has said which of the three it is."""
    return (
        getattr(validator, "as_condition", None) is not None or getattr(validator, "no_database_form", None) is not None
    )


def condition_for(validator, field):
    """The condition this validator wants at the column, or None when it wants none."""
    if getattr(validator, "no_database_form", None) is not None:
        return None
    build = getattr(validator, "as_condition", None)
    return build(field, validator) if build is not None else None


def _name(field, kind):
    """Unique across every model, and inside Postgres's identifier limit.

    Django wants constraint names unique project-wide rather than per table, so the table has to be
    in the name - several models have a `status`. That overruns the limit on the longest of them,
    and Postgres truncates rather than refusing, so the hash goes where it cannot be cut off.
    """
    full = f"{field.model._meta.db_table}_{field.column}_{kind}"
    if len(full) <= IDENTIFIER_LIMIT:
        return full
    digest = hashlib.blake2b(full.encode(), digest_size=4).hexdigest()
    return f"{full[: IDENTIFIER_LIMIT - len(digest) - 1]}_{digest}"


def _choices_condition(field):
    """`IN (...)` over the declared values, admitting whatever the column already admits.

    Django checks `choices` in `full_clean()` and nowhere else. `blank=True` on a text field means
    the empty string is storable and is not a choice, and a nullable column holds null outside the
    list by definition - a constraint refusing either refuses what the field was declared to allow.
    """
    allowed = [value for value, _label in field.flatchoices if value is not None]
    condition = models.Q(**{f"{field.name}__in": allowed})
    if field.null:
        condition |= models.Q(**{f"{field.name}__isnull": True})
    if field.blank and isinstance(field, models.CharField | models.TextField) and "" not in allowed:
        condition |= models.Q(**{field.name: ""})
    return condition


def _from_validators(field):
    """One constraint for everything the field's validators ask of the column.

    Combined rather than one apiece: two bounds on a number are one rule about it, and a reader of
    the schema should find them together.
    """
    conditions = [c for v in field.validators if (c := condition_for(inspect.unwrap(v), field)) is not None]
    if not conditions:
        return None
    combined = conditions[0]
    for condition in conditions[1:]:
        combined &= condition
    return models.CheckConstraint(condition=combined, name=_name(field, "validators"))


def constrain(model):
    """The constraints this model's own fields ask for, added once.

    Idempotent, because it is reached twice: once per model at `ready()`, and again from
    `class_prepared` for anything built afterwards.
    """
    if model._meta.abstract or model._meta.proxy or not issubclass(model, FullCleanOnSaveModel):
        return
    have = {constraint.name for constraint in model._meta.constraints}
    wanted = []
    for field in model._meta.concrete_fields:
        if field.choices:
            wanted.append(models.CheckConstraint(condition=_choices_condition(field), name=_name(field, "choices")))
        wanted.append(_from_validators(field))
    wanted = [constraint for constraint in wanted if constraint is not None and constraint.name not in have]
    if not wanted:
        return
    model._meta.constraints = [*model._meta.constraints, *wanted]
    # makemigrations reads original_attrs, which only a literal `class Meta` fills, so a runtime
    # append is invisible to it without this.
    model._meta.original_attrs["constraints"] = model._meta.constraints


def install():
    """From `CommonConfig.ready()`, which is the first moment every model is final.

    Both lifecycles, because neither covers the other: a model that builds its own columns from a
    `class_prepared` receiver is raced by a second receiver, and a test building one with
    `isolate_apps` prepares it long after `ready()`.
    """
    from django.apps import apps as django_apps

    teach_django_its_validators()
    for model in django_apps.get_models():
        constrain(model)
    # No dispatch_uid: Django keys a connection on the receiver's identity, and a second `ready()`
    # finds the same function object, so this cannot connect twice.
    class_prepared.connect(_constrain_prepared)


def _constrain_prepared(sender, **kwargs):
    constrain(sender)


def unclassified_validators(over=None):
    """Every hand-written validator that has not said which of the three it is.

    `_validators` rather than `validators`: Django appends its own from `max_length` and from a
    field's range, and nobody can wrap what they did not write. `over` names the models to read;
    the check leaves it out and gets the project.
    """
    from django.apps import apps as django_apps

    return sorted(
        {
            f"{model._meta.label}.{field.name} ({getattr(v, '__name__', None) or type(v).__name__})"
            for model in (django_apps.get_models() if over is None else over)
            if issubclass(model, FullCleanOnSaveModel) and not model._meta.abstract
            for field in model._meta.concrete_fields
            # isik wraps each one to make it skippable, so the wrapper is what the field holds.
            for v in map(inspect.unwrap, field._validators)
            if not classified(v)
        }
    )
