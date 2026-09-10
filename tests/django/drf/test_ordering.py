import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.models import F
from rest_framework.filters import OrderingFilter
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.drf.viewsets.ordering import DeclaredOrderingFilter, DeclaredOrderingMixin, ReverseOrderingMixin
from tests.testapp.models import Widget


class TestReverseOrderingMixin:
    def test_adds_a_reverse_counterpart_for_every_ordering_field(self):
        class WidgetViewSet(ReverseOrderingMixin):
            ordering_fields = ["created_at", "name"]
            filter_backends = [OrderingFilter]

        assert WidgetViewSet.ordering_fields == ["created_at", "name", "-created_at", "-name"]

    def test_opting_out_leaves_ordering_fields_untouched(self):
        class WidgetViewSet(ReverseOrderingMixin):
            allow_reverse_ordering = False
            ordering_fields = ["created_at"]
            filter_backends = [OrderingFilter]

        assert WidgetViewSet.ordering_fields == ["created_at"]

    def test_no_ordering_fields_declared_is_a_no_op(self):
        class WidgetViewSet(ReverseOrderingMixin):
            pass

        assert getattr(WidgetViewSet, "ordering_fields", []) == []

    def test_is_idempotent_across_multiple_levels_of_subclassing(self):
        class Base(ReverseOrderingMixin):
            ordering_fields = ["created_at"]
            filter_backends = [OrderingFilter]

        class Child(Base):
            pass

        class GrandChild(Child):
            pass

        assert GrandChild.ordering_fields == ["created_at", "-created_at"]

    def test_a_field_that_already_has_its_reverse_counterpart_is_not_duplicated(self):
        class WidgetViewSet(ReverseOrderingMixin):
            ordering_fields = ["created_at", "-created_at"]
            filter_backends = [OrderingFilter]

        assert WidgetViewSet.ordering_fields == ["created_at", "-created_at"]

    def test_raises_if_ordering_fields_set_without_ordering_filter(self):
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^BrokenViewSet sets ordering_fields but OrderingFilter \(or a subclass\) is "
            r"not in filter_backends - add it to REST_FRAMEWORK\['DEFAULT_FILTER_BACKENDS'\] "
            r"or set filter_backends directly\.$",
        ):

            class BrokenViewSet(ReverseOrderingMixin):
                ordering_fields = ["created_at"]
                filter_backends = []

    def test_raises_if_filter_backends_is_not_set_at_all(self):
        # getattr(cls, "filter_backends", ...)'s own default has to be an empty iterable, not
        # missing/None - a class that never sets filter_backends at all must still raise
        # ImproperlyConfigured, not crash with a TypeError trying to iterate it.
        with pytest.raises(ImproperlyConfigured, match="OrderingFilter"):

            class NoBackendsAttributeViewSet(ReverseOrderingMixin):
                ordering_fields = ["created_at"]

    def test_a_backend_subclass_satisfies_the_check(self):
        class CustomOrderingFilter(OrderingFilter):
            pass

        class WidgetViewSet(ReverseOrderingMixin):
            ordering_fields = ["created_at"]
            filter_backends = [CustomOrderingFilter]

        assert WidgetViewSet.ordering_fields == ["created_at", "-created_at"]

    def test_a_field_declared_only_in_negative_form_gets_no_forward_counterpart(self):
        # The mixin only ever adds a "-field" for a bare "field", never the reverse direction -
        # a field declared as just "-name" (no "name") is left as-is.
        class WidgetViewSet(ReverseOrderingMixin):
            ordering_fields = ["-name"]
            filter_backends = [OrderingFilter]

        assert WidgetViewSet.ordering_fields == ["-name"]


class TestDeclaredOrderingMixin:
    def test_no_declared_ordering_is_a_no_op_even_without_filter_backends(self):
        class WidgetViewSet(DeclaredOrderingMixin):
            pass

        assert WidgetViewSet.declared_ordering == {}

    def test_raises_if_declared_ordering_set_without_declared_ordering_filter(self):
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^BrokenViewSet sets declared_ordering but DeclaredOrderingFilter \(or a "
            r"subclass\) is not in filter_backends - add it to "
            r"REST_FRAMEWORK\['DEFAULT_FILTER_BACKENDS'\] or set filter_backends directly\.$",
        ):

            class BrokenViewSet(DeclaredOrderingMixin):
                declared_ordering = {"name": "count"}
                filter_backends = [OrderingFilter]

    def test_raises_if_filter_backends_is_not_set_at_all(self):
        with pytest.raises(ImproperlyConfigured, match="DeclaredOrderingFilter"):

            class NoBackendsAttributeViewSet(DeclaredOrderingMixin):
                declared_ordering = {"name": "count"}

    def test_a_backend_subclass_satisfies_the_check(self):
        class CustomDeclaredOrderingFilter(DeclaredOrderingFilter):
            pass

        class WidgetViewSet(DeclaredOrderingMixin):
            declared_ordering = {"name": "count"}
            filter_backends = [CustomDeclaredOrderingFilter]

        assert WidgetViewSet.declared_ordering == {"name": "count"}


@pytest.mark.django_db
class TestDeclaredOrderingFilter:
    @pytest.fixture
    def make_request(self):
        factory = APIRequestFactory()
        return lambda ordering: Request(factory.get("/", {"ordering": ordering}))

    def test_a_declared_key_resolves_to_its_target_field(self, make_request):
        Widget.objects.create(name="a", count=2)
        Widget.objects.create(name="b", count=1)

        class WidgetViewSet:
            declared_ordering = {"popularity": "count"}
            ordering_fields = []

        queryset = DeclaredOrderingFilter().filter_queryset(
            make_request("popularity"), Widget.objects.all(), WidgetViewSet()
        )
        assert [widget.count for widget in queryset] == [1, 2]

    def test_the_negative_form_flips_every_target_field(self, make_request):
        Widget.objects.create(name="a", count=2)
        Widget.objects.create(name="b", count=1)

        class WidgetViewSet:
            declared_ordering = {"popularity": "count"}
            ordering_fields = []

        queryset = DeclaredOrderingFilter().filter_queryset(
            make_request("-popularity"), Widget.objects.all(), WidgetViewSet()
        )
        assert [widget.count for widget in queryset] == [2, 1]

    def test_a_target_that_already_carries_a_sign_gets_flipped_too(self, make_request):
        Widget.objects.create(name="a", count=2)
        Widget.objects.create(name="b", count=1)

        class WidgetViewSet:
            declared_ordering = {"newest": "-count"}
            ordering_fields = []

        queryset = DeclaredOrderingFilter().filter_queryset(
            make_request("-newest"), Widget.objects.all(), WidgetViewSet()
        )
        assert [widget.count for widget in queryset] == [1, 2]

    def test_a_declared_key_can_map_to_several_fields(self, make_request):
        Widget.objects.create(name="b", count=1)
        Widget.objects.create(name="a", count=1)

        class WidgetViewSet:
            declared_ordering = {"identity": ("count", "name")}
            ordering_fields = []

        queryset = DeclaredOrderingFilter().filter_queryset(
            make_request("identity"), Widget.objects.all(), WidgetViewSet()
        )
        assert [widget.name for widget in queryset] == ["a", "b"]

    def test_an_expression_target_ignores_the_requests_sign_and_is_passed_through(self, make_request):
        Widget.objects.create(name="a", count=2)
        Widget.objects.create(name="b", count=1)

        class WidgetViewSet:
            declared_ordering = {"popularity": F("count").desc()}
            ordering_fields = []

        queryset = DeclaredOrderingFilter().filter_queryset(
            make_request("-popularity"), Widget.objects.all(), WidgetViewSet()
        )
        assert [widget.count for widget in queryset] == [2, 1]

    def test_a_non_declared_term_falls_back_to_plain_ordering_filter_behavior(self, make_request):
        Widget.objects.create(name="b", count=1)
        Widget.objects.create(name="a", count=2)

        class WidgetViewSet:
            declared_ordering = {}
            ordering_fields = ["name"]

        queryset = DeclaredOrderingFilter().filter_queryset(make_request("name"), Widget.objects.all(), WidgetViewSet())
        assert [widget.name for widget in queryset] == ["a", "b"]

    def test_an_invalid_term_that_isnt_declared_or_a_valid_ordering_field_is_dropped(self, make_request):
        Widget.objects.create(name="b", count=1)
        Widget.objects.create(name="a", count=2)

        class WidgetViewSet:
            declared_ordering = {}
            ordering_fields = []

        queryset = DeclaredOrderingFilter().filter_queryset(
            make_request("count"), Widget.objects.all(), WidgetViewSet()
        )
        # "count" isn't declared and isn't in ordering_fields, so it's stripped and no ordering
        # is applied at all - unlike the "name" case above, the queryset comes back untouched.
        assert {widget.name for widget in queryset} == {"a", "b"}
