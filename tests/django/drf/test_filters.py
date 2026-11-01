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


class TestMatchModeFilters:
    def filterset(self, **options):
        from django_filters import FilterSet

        from isik.django.drf import match_mode_filters
        from tests.testapp.models import Roster

        class RosterFilterSet(FilterSet):
            locals().update(match_mode_filters("tags", field_name="tags__label", **options))

            class Meta:
                model = Roster
                fields = []

        return RosterFilterSet

    def test_the_filter_and_its_mode_both_declared(self):
        from isik.django.drf import MatchModeChoiceFilter, MatchModeFilter

        filters = self.filterset().base_filters

        assert list(filters) == ["tags", "tags_match"]
        assert type(filters["tags"]) is MatchModeFilter
        assert (filters["tags"].field_name, filters["tags"].mode_param) == ("tags__label", "tags_match")
        assert type(filters["tags_match"]) is MatchModeChoiceFilter
        assert filters["tags_match"].extra["choices"] == [("any", "any"), ("all", "all")]
        assert (
            str(filters["tags_match"].extra["help_text"])
            == "Whether a row matches any of tags's values, or all of them."
        )

    def test_the_mode_filter_takes_a_filters_own_arguments(self):
        from isik.django.drf import MatchModeChoiceFilter

        made = MatchModeChoiceFilter("mode", choices=[("all", "all")])

        assert (made.field_name, made.extra["choices"]) == ("mode", [("all", "all")])

    def test_a_mode_param_named(self):
        assert list(self.filterset(mode_param="tags_mode").base_filters) == ["tags", "tags_mode"]
        assert self.filterset(mode_param="tags_mode").base_filters["tags"].mode_param == "tags_mode"

    def test_the_filters_own_options(self):
        assert self.filterset(label="Tags").base_filters["tags"].label == "Tags"

    def test_it_matches_as_the_filter_alone_does(self, db):
        from django.http import QueryDict

        from tests.testapp.models import Roster, Tag

        red, blue = Tag.objects.create(label="red"), Tag.objects.create(label="blue")
        Roster.objects.create(name="both").tags.add(red, blue)
        Roster.objects.create(name="red").tags.add(red)

        def names(query):
            return sorted(
                self.filterset()(QueryDict(query), queryset=Roster.objects.all()).qs.values_list("name", flat=True)
            )

        assert names("tags=red,blue&tags_match=all") == ["both"]
        assert names("tags=red,blue") == ["both", "red"]
        assert names("tags_match=all") == ["both", "red"]

    def test_another_mode_is_refused_by_its_choices(self, db):
        from django.http import QueryDict

        from tests.testapp.models import Roster

        made = self.filterset()(QueryDict("tags=red&tags_match=most"), queryset=Roster.objects.all())

        assert not made.is_valid()
        assert list(made.errors) == ["tags_match"]

    def test_a_schema_publishes_the_mode(self):
        from django_filters.rest_framework import DjangoFilterBackend
        from rest_framework import serializers

        from isik.django.drf.viewsets import ViewSetRegistryExemption
        from isik.django.drf.viewsets.base import BaseModelViewSet
        from tests.django.drf.test_spectacular import generate_schema
        from tests.testapp.models import Roster

        class RosterSerializer(serializers.ModelSerializer):
            class Meta:
                model = Roster
                fields = ["id", "name"]

        class RosterViewSet(BaseModelViewSet):
            model = Roster
            endpoint = "rosters"
            serializer_class = RosterSerializer
            filter_backends = [DjangoFilterBackend]
            filterset_class = self.filterset()
            exempt_from_registry = ViewSetRegistryExemption(reason="a test's own class, defined again on every run")

        schema = generate_schema((RosterViewSet, "rosters"))

        parameters = {parameter["name"]: parameter for parameter in schema["paths"]["/rosters/"]["get"]["parameters"]}
        assert parameters["tags_match"]["schema"] == {"type": "string", "enum": ["all", "any"]}
        assert parameters["tags_match"]["description"].startswith("Whether a row matches any of tags's values")


class TestModelMatchModeFilters:
    @pytest.fixture
    def tagged(self, db):
        from tests.testapp.models import Roster, Tag

        red, blue, green = (Tag.objects.create(label=label) for label in ("red", "blue", "green"))
        Roster.objects.create(name="both").tags.add(red, blue)
        Roster.objects.create(name="red").tags.add(red, green)
        Roster.objects.create(name="none")
        return {"red": red.pk, "blue": blue.pk, "green": green.pk}

    def filterset(self):
        from django_filters import FilterSet

        from isik.django.drf import match_mode_filters
        from tests.testapp.models import Roster, Tag

        class RosterFilterSet(FilterSet):
            locals().update(match_mode_filters("tags", field_name="tags", queryset=Tag.objects.all(), label="Tags"))

            class Meta:
                model = Roster
                fields = []

        return RosterFilterSet

    def run(self, pairs):
        from django.http import QueryDict

        from tests.testapp.models import Roster

        query = QueryDict(mutable=True)
        for key, value in pairs:
            query.appendlist(key, str(value))
        return self.filterset()(query, queryset=Roster.objects.all())

    def names(self, pairs):
        return sorted(self.run(pairs).qs.values_list("name", flat=True))

    def test_a_queryset_makes_it_take_rows(self):
        from isik.django.drf import MatchModeChoiceFilter, ModelMatchModeFilter
        from tests.testapp.models import Tag

        filters = self.filterset().base_filters

        assert type(filters["tags"]) is ModelMatchModeFilter
        assert filters["tags"].queryset.model is Tag
        assert (filters["tags"].field_name, filters["tags"].mode_param, filters["tags"].label) == (
            "tags",
            "tags_match",
            "Tags",
        )
        assert type(filters["tags_match"]) is MatchModeChoiceFilter

    def test_its_field_name_whatever_its_called(self):
        from django_filters import FilterSet

        from isik.django.drf import match_mode_filters
        from tests.testapp.models import Roster, Tag

        class RosterFilterSet(FilterSet):
            locals().update(match_mode_filters("labelled", field_name="tags", queryset=Tag.objects.all()))

            class Meta:
                model = Roster
                fields = []

        assert RosterFilterSet.base_filters["labelled"].field_name == "tags"

    def test_any_by_default(self, tagged):
        assert self.names([("tags", tagged["blue"]), ("tags", tagged["green"])]) == ["both", "red"]

    def test_all(self, tagged):
        pairs = [("tags", tagged["red"]), ("tags", tagged["blue"]), ("tags_match", "all")]

        assert self.names(pairs) == ["both"]

    def test_a_row_given_twice_counts_once(self, tagged):
        pairs = [("tags", tagged["red"]), ("tags", tagged["red"]), ("tags", tagged["blue"]), ("tags_match", "all")]

        assert self.names(pairs) == ["both"]

    def test_no_value_leaves_every_row(self, tagged):
        assert self.names([("tags_match", "all")]) == ["both", "none", "red"]

    def test_a_value_naming_no_row_is_refused(self, tagged):
        import uuid

        made = self.run([("tags", tagged["red"]), ("tags", uuid.uuid4())])

        assert not made.is_valid()
        assert list(made.errors) == ["tags"]

    def test_a_schema_publishes_both(self):
        from django_filters.rest_framework import DjangoFilterBackend
        from rest_framework import serializers

        from isik.django.drf.viewsets import ViewSetRegistryExemption
        from isik.django.drf.viewsets.base import BaseModelViewSet
        from tests.django.drf.test_spectacular import generate_schema
        from tests.testapp.models import Roster

        class RosterSerializer(serializers.ModelSerializer):
            class Meta:
                model = Roster
                fields = ["id", "name"]

        class RosterViewSet(BaseModelViewSet):
            model = Roster
            endpoint = "rosters"
            serializer_class = RosterSerializer
            filter_backends = [DjangoFilterBackend]
            filterset_class = self.filterset()
            exempt_from_registry = ViewSetRegistryExemption(reason="a test's own class, defined again on every run")

        schema = generate_schema((RosterViewSet, "rosters"))

        parameters = {parameter["name"]: parameter for parameter in schema["paths"]["/rosters/"]["get"]["parameters"]}
        assert parameters["tags"]["schema"]["type"] == "array"
        assert parameters["tags"]["explode"] is True
        assert parameters["tags_match"]["schema"] == {"type": "string", "enum": ["all", "any"]}
