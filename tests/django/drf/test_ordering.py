import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.filters import OrderingFilter

from isik.django.drf.viewsets.ordering import ReverseOrderingMixin


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
        with pytest.raises(ImproperlyConfigured, match="OrderingFilter"):

            class BrokenViewSet(ReverseOrderingMixin):
                ordering_fields = ["created_at"]
                filter_backends = []

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
