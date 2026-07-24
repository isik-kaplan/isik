from rest_framework.viewsets import ModelViewSet

from isik.common.utils.required_attributes import REQUIRED, RequiredAttributesMixin
from isik.django.drf.viewsets.action_serializer_class import ActionSerializerClassMixin
from isik.django.drf.viewsets.filterset import FilterSetMixin
from isik.django.drf.viewsets.ordering import ReverseOrderingMixin
from isik.django.drf.viewsets.protected_destroy import ProtectedDestroyMixin
from isik.django.drf.viewsets.registry import ViewSetRegistryMixin


class BaseModelViewSet(
    RequiredAttributesMixin,
    ViewSetRegistryMixin,
    ActionSerializerClassMixin,
    ProtectedDestroyMixin,
    ReverseOrderingMixin,
    FilterSetMixin,
    ModelViewSet,
):
    """
    Everything above composed together - see each mixin's own docstring for what it adds:
    RequiredAttributesMixin (`model`/`endpoint`/`serializer_class` must be set explicitly, fails
    fast at class-definition time otherwise), ViewSetRegistryMixin (model -> viewset lookup),
    ActionSerializerClassMixin (`serializer_class_action_map`), ProtectedDestroyMixin (a clean 400
    instead of a 500 on ProtectedError), ReverseOrderingMixin (`-field` ordering for free),
    FilterSetMixin (`filterset_class` built from `filterset_fields`/`declared_filters`).

    `model` is the source of truth for the queryset - not `serializer_class.Meta.model` - so it
    has to be set even though the serializer already implies it, matching the "required
    explicit" choice made for `endpoint` too.

    Pick and compose the individual mixins directly instead, if a project doesn't want all of this.
    """

    required_attributes = ["model", "endpoint", "serializer_class"]
    model = REQUIRED
    endpoint = REQUIRED
    serializer_class = REQUIRED

    def get_queryset(self):
        return self.model.objects.all()
