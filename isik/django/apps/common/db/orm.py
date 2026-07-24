from contextlib import suppress

from django.core.exceptions import ValidationError
from django.db.models import BooleanField, Case, Value, When


def starts_with(field, prefix):
    return Case(
        When(**{f"{field}__startswith": prefix}, then=Value(True)),
        default=Value(False),
        output_field=BooleanField(),
    )


def get_object_or_none(model, **kwargs):
    """
    Like model.objects.get(**kwargs), but returns None instead of raising
    when no match is found, or when a lookup value fails field validation
    (e.g. an invalid UUID string for a UUID primary key).
    """
    with suppress(model.DoesNotExist, ValidationError):
        return model.objects.get(**kwargs)
