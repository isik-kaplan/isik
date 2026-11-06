"""
Uniqueness for a field a serializer writes to a row related to its own instance - shared by the flattened
one-to-one and singleton fields, which both do.
"""

from rest_framework.exceptions import ValidationError
from rest_framework.validators import UniqueValidator


class RelatedRowUniqueValidator(UniqueValidator):
    """
    `unique=True` on `attribute` of a related row. DRF's `UniqueValidator` leaves out the serializer's own
    instance - here the parent, not the row the value is written to - so a value that row already holds
    would be refused as taken. This one leaves out `rows(instance)`, the row the parent holds now, in the
    same one query.
    """

    def __init__(self, unique, *, attribute, rows):
        super().__init__(unique.queryset, unique.message, unique.lookup)
        self.attribute = attribute
        self.rows = rows

    def __call__(self, value, serializer_field):
        taken = self.filter_queryset(value, self.queryset, self.attribute)
        instance = serializer_field.parent.instance
        if instance is not None:
            taken = taken.exclude(pk__in=self.rows(instance).values("pk"))
        if taken.exists():
            raise ValidationError(self.message, code="unique")


def related_row_uniqueness(validators, *, attribute, rows):
    """`validators`, each `UniqueValidator` in them made a `RelatedRowUniqueValidator`."""
    return [
        RelatedRowUniqueValidator(validator, attribute=attribute, rows=rows)
        if isinstance(validator, UniqueValidator)
        else validator
        for validator in validators
    ]
