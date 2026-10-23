from contextlib import suppress

from django.core.exceptions import ValidationError
from django.db.models import Case, Value, When
from django.db.models.expressions import DatabaseDefault
from django.db.models.functions import Now
from django.utils import timezone

from isik._internal.translation import gettext as _


def starts_with(field, prefix):
    return Case(When(**{f"{field}__startswith": prefix}, then=Value(True)), default=Value(False))


def get_object_or_none(model, **kwargs):
    """
    Like model.objects.get(**kwargs), but returns None instead of raising
    when no match is found, or when a lookup value fails field validation
    (e.g. an invalid UUID string for a UUID primary key).
    """
    with suppress(model.DoesNotExist, ValidationError):
        return model.objects.get(**kwargs)


def _python_value(field):
    default = field.db_default
    if isinstance(default, Now):
        return timezone.now()
    if isinstance(default, Value):
        return default.value
    # A literal is kept as it was given, not as a Value.
    if not hasattr(default, "resolve_expression"):
        return default
    raise ValueError(
        _("%(field)s's db_default %(default)r has no value in Python - give the field one before encoding.")
        % {"field": field, "default": default}
    )


def with_python_defaults(instance):
    """
    `instance`, unsaved, with each field the database would default (`db_default`) given its value in
    Python instead of the placeholder Django leaves until the row is written - for code that encodes an
    unsaved instance, which can't encode the placeholder: a library serializing a user it hasn't saved
    yet, say. `Now()` becomes `timezone.now()` and a literal its value; another expression is refused,
    naming the field.

        user = with_python_defaults(User(email=email))
        json.dumps(model_to_dict(user), cls=DjangoJSONEncoder)

    The row, once saved, holds the database's own values - `refresh_from_db()` reads them.
    """
    for field in instance._meta.concrete_fields:
        if isinstance(getattr(instance, field.attname), DatabaseDefault):
            setattr(instance, field.attname, _python_value(field))
    return instance
