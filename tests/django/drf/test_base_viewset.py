import pytest
from rest_framework import serializers, status
from rest_framework.test import APIRequestFactory

from isik.django.drf.viewsets.base import BaseModelViewSet
from isik.django.drf.viewsets.registry import ViewSetRegistryMixin
from tests.testapp.models import Comment, Tag, Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class WidgetViewSet(BaseModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
    filterset_fields = ["name"]
    ordering_fields = ["created_at"]


class TestBaseModelViewSet:
    def test_missing_required_attribute_raises_at_class_definition_time(self):
        with pytest.raises(TypeError, match="must define a `endpoint` attribute"):

            class BrokenViewSet(BaseModelViewSet):
                model = Tag
                serializer_class = WidgetSerializer

    def test_registers_itself_in_the_viewset_registry(self):
        assert ViewSetRegistryMixin.get_for_model(Widget) is WidgetViewSet

    def test_missing_attribute_check_runs_before_registration_not_after(self):
        # Regression: RequiredAttributesMixin used to run last in the __init_subclass__ cascade
        # (cooperative super() calls run own logic in reverse-MRO order by default), so a
        # half-configured subclass got registered into ViewSetRegistryMixin's model_map *before*
        # the missing-attribute error was raised - meaning a fixed-up retry under the same model
        # would then fail with a spurious "already registered" instead of succeeding.
        with pytest.raises(TypeError, match="must define a `endpoint` attribute"):

            class BrokenViewSet(BaseModelViewSet):
                model = Tag
                serializer_class = WidgetSerializer

        assert ViewSetRegistryMixin.get_for_model(Tag) is None

    def test_get_queryset_comes_from_model_not_serializer_meta(self):
        Widget.objects.create(name="bolt", count=1)
        view = WidgetViewSet()
        assert list(view.get_queryset()) == list(Widget.objects.all())

    def test_ordering_fields_gets_its_reverse_counterpart(self):
        assert WidgetViewSet.ordering_fields == ["created_at", "-created_at"]

    def test_filterset_class_is_built_from_filterset_fields(self):
        assert WidgetViewSet.filterset_class.Meta.model is Widget
        assert WidgetViewSet.filterset_class.Meta.fields == ["name"]

    def test_destroy_reports_protected_by_on_a_real_protected_error(self):
        widget = Widget.objects.create(name="bolt", count=1)
        Comment.objects.create(body="hi", widget=widget)
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        view = WidgetViewSet.as_view({"delete": "destroy"})
        response = view(request, pk=widget.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"protected_by": [{"model": "Comment", "field": "widget"}]}

    def test_list_action_uses_the_action_serializer_class_map_when_set(self):
        class ListOnlySerializer(serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id"]

        class ScopedViewSet(WidgetViewSet):
            model = Widget
            endpoint = "scoped-widgets"
            exempt_from_registry = True
            serializer_class_action_map = {"list": ListOnlySerializer}

        view = ScopedViewSet()
        view.action = "list"
        assert view.get_serializer_class() is ListOnlySerializer
        view.action = "retrieve"
        assert view.get_serializer_class() is WidgetSerializer
