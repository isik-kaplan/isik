import pytest
from django.core.exceptions import ImproperlyConfigured
from django_filters import CharFilter
from django_filters.rest_framework import DjangoFilterBackend, FilterSet

from isik.django.drf.viewsets.filterset import FilterSetMixin
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class WidgetViewSet(FilterSetMixin):
    model = Widget
    filterset_fields = ["name", "count"]
    filter_backends = [DjangoFilterBackend]


class WidgetWithDeclaredFilterViewSet(FilterSetMixin):
    model = Widget
    filterset_fields = ["count"]
    declared_filters = {"name": CharFilter(lookup_expr="icontains")}
    filter_backends = [DjangoFilterBackend]


class CustomFilterSetBase(FilterSet):
    """A filterset_base override carrying its own Meta attribute unrelated to model/fields, to
    prove the generated Meta actually inherits from it rather than starting from a blank slate."""

    class Meta:
        custom_marker = "hi"


class WidgetViewSetWithCustomFilterSetBase(FilterSetMixin):
    model = Widget
    filterset_base = CustomFilterSetBase
    filterset_fields = ["name"]
    filter_backends = [DjangoFilterBackend]


class WidgetViewSetWithDictStyleFilterFields(FilterSetMixin):
    model = Widget
    filterset_fields = {"name": ["exact", "icontains"], "count": ["exact", "gte"]}
    filter_backends = [DjangoFilterBackend]


class TestFilterSetMixin:
    def test_builds_a_filterset_class_for_the_model(self):
        assert issubclass(WidgetViewSet.filterset_class, FilterSet)
        assert WidgetViewSet.filterset_class.Meta.model is Widget
        assert WidgetViewSet.filterset_class.Meta.fields == ["name", "count"]

    def test_declared_filters_are_included(self):
        filterset = WidgetWithDeclaredFilterViewSet.filterset_class
        assert "name" in filterset.base_filters
        assert isinstance(filterset.base_filters["name"], CharFilter)

    def test_filtering_actually_works_against_the_database(self):
        Widget.objects.create(name="bolt", count=1)
        Widget.objects.create(name="nut", count=2)
        filterset = WidgetWithDeclaredFilterViewSet.filterset_class(data={"name": "bo"}, queryset=Widget.objects.all())
        assert [widget.name for widget in filterset.qs] == ["bolt"]

    def test_no_filter_fields_declared_is_a_no_op_even_without_filter_backends(self):
        class PlainViewSet(FilterSetMixin):
            model = Widget

        assert PlainViewSet.filterset_class.Meta.fields == {}

    def test_raises_if_filterset_fields_set_without_django_filter_backend(self):
        with pytest.raises(ImproperlyConfigured, match="DjangoFilterBackend"):

            class BrokenViewSet(FilterSetMixin):
                model = Widget
                filterset_fields = ["name"]
                filter_backends = []

    def test_raises_if_declared_filters_set_without_django_filter_backend(self):
        with pytest.raises(ImproperlyConfigured, match="DjangoFilterBackend"):

            class BrokenViewSet(FilterSetMixin):
                model = Widget
                declared_filters = {"name": CharFilter(lookup_expr="icontains")}
                filter_backends = []

    def test_a_backend_subclass_satisfies_the_check(self):
        class CustomDjangoFilterBackend(DjangoFilterBackend):
            pass

        class WidgetViewSetWithSubclassedBackend(FilterSetMixin):
            model = Widget
            filterset_fields = ["name"]
            filter_backends = [CustomDjangoFilterBackend]

        assert WidgetViewSetWithSubclassedBackend.filterset_class.Meta.fields == ["name"]

    def test_filterset_base_override_is_used_and_its_own_meta_is_inherited(self):
        filterset = WidgetViewSetWithCustomFilterSetBase.filterset_class
        assert issubclass(filterset, CustomFilterSetBase)
        assert filterset.Meta.custom_marker == "hi"
        assert filterset.Meta.model is Widget
        assert filterset.Meta.fields == ["name"]

    def test_dict_style_filterset_fields_generates_a_filter_per_lookup(self):
        filterset = WidgetViewSetWithDictStyleFilterFields.filterset_class
        assert filterset.Meta.fields == {"name": ["exact", "icontains"], "count": ["exact", "gte"]}
        assert "name" in filterset.base_filters
        assert "name__icontains" in filterset.base_filters
        assert "count" in filterset.base_filters
        assert "count__gte" in filterset.base_filters
