import pytest
from rest_framework import serializers, status
from rest_framework.test import APIRequestFactory

from isik.django.drf.viewsets import ViewSetRegistryExemption
from isik.django.drf.viewsets.base import BaseModelViewSet
from isik.django.drf.viewsets.ordering import DeclaredOrderingFilter
from isik.django.drf.viewsets.registry import ViewSetRegistryMixin
from tests.django.drf.support import UnguardedModelViewSet
from tests.testapp.models import Comment, Tag, Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class WidgetViewSet(UnguardedModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
    filterset_fields = ["name"]
    ordering_fields = ["created_at"]


class WidgetViewSetWithDeclaredOrdering(BaseModelViewSet):
    model = Widget
    endpoint = "declared-ordering-widgets"
    exempt_from_registry = ViewSetRegistryExemption(reason="a test's own class, defined again on every run")
    serializer_class = WidgetSerializer
    declared_ordering = {"popularity": "count"}
    filter_backends = [DeclaredOrderingFilter]


class TestBaseModelViewSet:
    def test_missing_required_attribute_raises_at_class_definition_time(self):
        with pytest.raises(TypeError, match="must define a `endpoint` attribute"):

            class BrokenViewSet(BaseModelViewSet):
                model = Tag
                serializer_class = WidgetSerializer

    def test_registers_itself_in_the_viewset_registry(self):
        # In the registry of the base it subclasses - a test-only one, see UnguardedModelViewSet.
        assert UnguardedModelViewSet.get_for_model(Widget) is WidgetViewSet

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

    def test_declared_ordering_resolves_to_its_target_field(self):
        Widget.objects.create(name="a", count=2)
        Widget.objects.create(name="b", count=1)
        request = APIRequestFactory().get("/declared-ordering-widgets/", {"ordering": "-popularity"})
        view = WidgetViewSetWithDeclaredOrdering.as_view({"get": "list"})
        response = view(request)
        assert [widget["count"] for widget in response.data] == [2, 1]

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
            exempt_from_registry = ViewSetRegistryExemption(reason="a test's own class, defined again on every run")
            serializer_class_action_map = {"list": ListOnlySerializer}

        view = ScopedViewSet()
        view.action = "list"
        assert view.get_serializer_class() is ListOnlySerializer
        view.action = "retrieve"
        assert view.get_serializer_class() is WidgetSerializer


class TestTheWriteGuardsItCarries:
    def test_are_idempotency_keys_and_preconditions(self):
        from isik.django.apps.idempotency.drf import IdempotencyMixin
        from isik.django.drf.viewsets import PreconditionMixin

        assert issubclass(BaseModelViewSet, IdempotencyMixin)
        assert issubclass(BaseModelViewSet, PreconditionMixin)
        # A key's replay runs in initial(), a precondition in get_object() - either order would work.
        assert BaseModelViewSet.__mro__.index(IdempotencyMixin) < BaseModelViewSet.__mro__.index(PreconditionMixin)


def test_no_mixin_of_isiks_overrides_get_serializer():
    # Overriding it flips drf-spectacular onto calling it for every view during schema generation, with no
    # request for a serializer context to read - a project's view breaks for adding an unrelated mixin.
    import importlib
    import inspect
    import pkgutil

    import isik.django.drf.viewsets as viewsets

    modules = [
        importlib.import_module(f"{viewsets.__name__}.{info.name}") for info in pkgutil.iter_modules(viewsets.__path__)
    ]
    modules.append(importlib.import_module("isik.django.apps.notifications.drf"))
    assert len(modules) == 12
    overriding = [
        f"{cls.__module__}.{cls.__name__}"
        for module in modules
        for cls in vars(module).values()
        if inspect.isclass(cls) and cls.__module__.startswith("isik.") and "get_serializer" in vars(cls)
    ]

    assert overriding == []
