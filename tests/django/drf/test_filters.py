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


class TestMatchModeFilter:
    @pytest.fixture
    def rosters(self, db):
        from tests.testapp.models import Roster, Tag

        red, blue, green = (Tag.objects.create(label=label) for label in ("red", "blue", "green"))
        both = Roster.objects.create(name="both")
        both.tags.add(red, blue)
        red_only = Roster.objects.create(name="red")
        red_only.tags.add(red, green)
        # Two tags of one label: two rows of the join, one value matched.
        twice_red = Roster.objects.create(name="twice red")
        twice_red.tags.add(red, Tag.objects.create(label="red"))
        Roster.objects.create(name="none")
        return Roster

    def names(self, rosters, query):
        from django.http import QueryDict
        from django_filters import FilterSet

        from isik.django.drf import MatchModeFilter

        class RosterFilterSet(FilterSet):
            tags = MatchModeFilter("tags__label", mode_param="tags_match")

            class Meta:
                model = rosters
                fields = []

        return sorted(
            RosterFilterSet(QueryDict(query), queryset=rosters.objects.all()).qs.values_list("name", flat=True)
        )

    def test_takes_a_filters_own_options(self):
        from isik.django.drf import MatchModeFilter

        assert MatchModeFilter("tags__label", mode_param="tags_match", label="Tags").label == "Tags"

    def test_any_by_default(self, rosters):
        assert self.names(rosters, "tags=red,blue") == ["both", "red", "twice red"]

    def test_any_named(self, rosters):
        assert self.names(rosters, "tags=blue,green&tags_match=any") == ["both", "red"]

    def test_all(self, rosters):
        assert self.names(rosters, "tags=red,blue&tags_match=all") == ["both"]

    def test_all_counts_a_value_given_twice_once(self, rosters):
        assert self.names(rosters, "tags=red,red,blue&tags_match=all") == ["both"]

    def test_all_of_one_is_any_of_one(self, rosters):
        assert self.names(rosters, "tags=red&tags_match=all") == ["both", "red", "twice red"]

    def test_no_value_leaves_every_row(self, rosters):
        assert self.names(rosters, "tags_match=all") == ["both", "none", "red", "twice red"]

    def test_a_row_is_listed_once(self, rosters):
        assert self.names(rosters, "tags=red,blue") == ["both", "red", "twice red"]

    def test_another_mode_is_refused_naming_its_parameter(self, rosters):
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError) as raised:
            self.names(rosters, "tags=red&tags_match=most")

        assert raised.value.detail == {"tags_match": ["Must be one of: any, all."]}
