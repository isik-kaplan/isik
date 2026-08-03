import pytest
from rest_framework import serializers

from isik.django.drf.viewsets.base import BaseModelViewSet
from isik.django.drf.viewsets.schema_generation import none_during_schema_generation
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class UserScopedWidgetViewSet(BaseModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
    exempt_from_registry = True

    @none_during_schema_generation
    def get_queryset(self):
        return super().get_queryset().filter(name="mine")


class TestNoneDuringSchemaGeneration:
    def test_returns_the_real_queryset_for_a_normal_request(self):
        Widget.objects.create(name="mine", count=1)
        Widget.objects.create(name="not-mine", count=2)
        view = UserScopedWidgetViewSet()
        assert [w.name for w in view.get_queryset()] == ["mine"]

    def test_returns_an_empty_queryset_during_schema_generation(self):
        Widget.objects.create(name="mine", count=1)
        view = UserScopedWidgetViewSet()
        view.swagger_fake_view = True
        assert list(view.get_queryset()) == []
