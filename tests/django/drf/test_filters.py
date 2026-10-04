import pytest
from django_filters import CharFilter

from isik.django.drf.filters import NarrowingFilterMixin, make_filters
from tests.testapp.models import Widget


class FakeFilter:
    """Stands in for a django-filter Filter subclass without adding a dependency on it."""

    def __init__(self, field_name, lookup_expr):
        self.field_name = field_name
        self.lookup_expr = lookup_expr


def test_exact_lookup_uses_the_bare_field_name():
    result = make_filters("created_at", FakeFilter, ["exact"])
    assert set(result) == {"created_at"}
    assert result["created_at"].lookup_expr == "exact"
    assert result["created_at"].field_name == "created_at"


def test_non_exact_lookups_are_suffixed_with_the_expression():
    result = make_filters("created_at", FakeFilter, ["exact", "gte", "lte"])
    assert set(result) == {"created_at", "created_at__gte", "created_at__lte"}
    assert result["created_at__gte"].lookup_expr == "gte"
    assert result["created_at__gte"].field_name == "created_at"


def test_no_lookup_expressions_produces_an_empty_dict():
    assert make_filters("name", FakeFilter, []) == {}


class TestNarrowingFilterMixin:
    """An unset filter leaves the queryset alone; a set one narrows it - the guard written once."""

    class NameStartsWith(NarrowingFilterMixin, CharFilter):
        def narrow(self, qs, value):
            return qs.filter(name__startswith=value)

    @pytest.mark.django_db
    @pytest.mark.parametrize("value", ["", None, [], (), {}])
    def test_an_empty_value_leaves_the_queryset_alone(self, value):
        queryset = Widget.objects.all()

        assert self.NameStartsWith().filter(queryset, value) is queryset

    @pytest.mark.django_db
    def test_a_value_narrows_through_the_subclass(self):
        Widget.objects.create(name="bolt")
        Widget.objects.create(name="nut")

        narrowed = self.NameStartsWith().filter(Widget.objects.all(), "bo")

        assert list(narrowed.values_list("name", flat=True)) == ["bolt"]

    def test_the_bare_mixin_says_what_to_implement(self):
        class Unfinished(NarrowingFilterMixin, CharFilter):
            pass

        with pytest.raises(NotImplementedError):
            Unfinished().filter(None, "x")
