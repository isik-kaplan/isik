"""ConcurrencyTokenField - see its own docstring."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from isik.django.apps.common.db.concurrency import concurrency_etag


@extend_schema_field(OpenApiTypes.STR)
class ConcurrencyTokenField(serializers.Field):
    """
    The object's concurrency token as `PreconditionMixin` sends it in `ETag` - the same string,
    quotes and all - so each item of a list, which has no `ETag` of its own, carries the one to send
    back in `If-Match`:

        class WidgetSerializer(serializers.ModelSerializer):
            etag = ConcurrencyTokenField()

    Always read-only, and always the whole object as its source.
    """

    def __init__(self, **kwargs):
        super().__init__(**{**kwargs, "source": "*", "read_only": True})

    def to_representation(self, value):
        return concurrency_etag(value)
