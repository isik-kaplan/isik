from rest_framework.serializers import ModelSerializer

from isik.django.drf.serializers.conditional_serializer import ConditionalSerializerMixin
from isik.django.drf.serializers.create_only import CreateOnlyFieldsMixin
from isik.django.drf.serializers.meta_combining import MetaCombiningMixin
from isik.django.drf.serializers.registry import ModelSerializerRegistryMixin
from isik.django.drf.serializers.request_context import RequestContextMixin


class BaseModelSerializer(
    ModelSerializerRegistryMixin,
    CreateOnlyFieldsMixin,
    MetaCombiningMixin,
    RequestContextMixin,
    ConditionalSerializerMixin,
    ModelSerializer,
):
    """
    Everything above composed together - see each mixin's own docstring for what it adds:
    ModelSerializerRegistryMixin (model -> serializer lookup), CreateOnlyFieldsMixin
    (`Meta.create_only_fields`), MetaCombiningMixin (`Meta.relational_fields` merges with any
    `_Meta` set further up the hierarchy - empty by default), RequestContextMixin
    (`current_request()`/`current_user()`), ConditionalSerializerMixin (`?include=`/`?only=`/
    `?exclude=`).

    Pick and compose the individual mixins directly instead, if a project doesn't want all of this.
    """

    meta_fields_to_combine = ["relational_fields"]

    class _Meta:
        relational_fields = {}
