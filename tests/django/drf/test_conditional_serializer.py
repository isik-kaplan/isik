import pytest
from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.drf.serializers.conditional_serializer import (
    ConditionalSerializerMixin,
    relational_serializer,
    serializer_method_include,
)
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class OwnerSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username"]
        relational_fields = {
            "manager": relational_serializer("tests.django.drf.test_conditional_serializer.OwnerSerializer"),
            "reports": relational_serializer("tests.django.drf.test_conditional_serializer.OwnerSerializer", many=True),
        }


class WidgetSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        relational_fields = {"owner": relational_serializer(OwnerSerializer)}


class PlainWidgetSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    """No Meta.relational_fields declared at all, to prove the mixin no-ops safely."""

    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class NoMetaAtAllSerializer(ConditionalSerializerMixin, serializers.Serializer):
    """No Meta class whatsoever - a plain Serializer, unlike ModelSerializer, doesn't require one."""

    name = serializers.CharField()
    count = serializers.IntegerField()


class WidgetSerializerWithMethodField(ConditionalSerializerMixin, serializers.ModelSerializer):
    owner_detail = serializers.SerializerMethodField()

    class Meta:
        model = Widget
        fields = ["id", "name", "owner_detail"]

    @serializer_method_include
    def get_owner_detail(self, obj):
        return OwnerSerializer(obj.owner, context=self.context)


@pytest.fixture
def make_request():
    factory = APIRequestFactory()
    return lambda *args, **kwargs: Request(factory.get(*args, **kwargs))


@pytest.fixture
def widget(django_user_model):
    owner = django_user_model.objects.create_user(username="alice", password="password")
    return Widget.objects.create(name="bolt", count=1, owner=owner)


@pytest.fixture
def management_chain(django_user_model):
    # alice reports to bob, who reports to carol - three levels to exercise dotted includes.
    carol = django_user_model.objects.create_user(username="carol", email="carol@example.com", password="password")
    bob = django_user_model.objects.create_user(
        username="bob", email="bob@example.com", password="password", manager=carol
    )
    alice = django_user_model.objects.create_user(
        username="alice", email="alice@example.com", password="password", manager=bob
    )
    return alice, bob, carol


class TestConditionalSerializerMixin:
    def test_relational_field_is_excluded_by_default(self, widget, make_request):
        serializer = WidgetSerializer(widget, context={"request": make_request("/")})
        assert "owner" not in serializer.data

    def test_relational_field_is_included_when_requested(self, widget, make_request):
        request = make_request("/", {"include": "owner"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert serializer.data["owner"] == {"id": widget.owner_id, "username": "alice"}

    def test_comma_separated_include_values_are_split(self, widget, make_request):
        request = make_request("/", {"include": "owner,some_other_field"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "owner" in serializer.data

    def test_repeated_include_query_params_are_combined(self, widget, make_request):
        request = make_request("/?include=some_other_field&include=owner")
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "owner" in serializer.data

    def test_requesting_an_unrelated_name_does_not_include_anything(self, widget, make_request):
        request = make_request("/", {"include": "some_other_field"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "owner" not in serializer.data

    def test_no_request_in_context_does_not_crash_and_excludes_the_field(self, widget):
        serializer = WidgetSerializer(widget, context={})
        assert "owner" not in serializer.data

    def test_a_get_without_getlist_is_ignored_safely(self, widget):
        class FakeRequest:
            GET = {"include": ["owner"]}  # a plain dict has no .getlist, unlike a QueryDict

        serializer = WidgetSerializer(widget, context={"request": FakeRequest()})
        assert "owner" not in serializer.data

    def test_works_as_a_no_op_when_meta_has_no_relational_fields(self, widget, make_request):
        request = make_request("/", {"include": "owner"})
        serializer = PlainWidgetSerializer(widget, context={"request": request})
        assert set(serializer.data) == {"id", "name", "count"}

    def test_does_not_crash_on_a_serializer_with_no_meta_class_at_all(self, widget, make_request):
        request = make_request("/", {"only": "name"})
        serializer = NoMetaAtAllSerializer(widget, context={"request": request})
        assert serializer.data == {"name": "bolt"}


class TestRelationalSerializer:
    def test_resolves_a_class_reference_directly(self):
        instance = relational_serializer(OwnerSerializer)()
        assert isinstance(instance, OwnerSerializer)
        assert instance.read_only is True

    def test_resolves_a_string_reference_lazily(self):
        instance = relational_serializer("tests.django.drf.test_conditional_serializer.OwnerSerializer")()
        assert isinstance(instance, OwnerSerializer)

    def test_kwargs_pass_through_and_can_override_read_only(self):
        instance = relational_serializer(OwnerSerializer, read_only=False)()
        assert instance.read_only is False

    def test_each_call_returns_a_fresh_instance(self):
        factory = relational_serializer(OwnerSerializer)
        assert factory() is not factory()


class TestDottedIncludes:
    def test_a_dotted_path_reaches_into_the_nested_serializer(self, management_chain, make_request):
        alice, bob, _carol = management_chain
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request("/", {"include": "owner.manager"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert serializer.data["owner"]["manager"] == {"id": bob.id, "username": "bob"}

    def test_requesting_only_the_leaf_path_still_includes_its_ancestor(self, management_chain, make_request):
        alice, _bob, _carol = management_chain
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request("/", {"include": "owner.manager"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "owner" in serializer.data

    def test_a_shallower_include_does_not_pull_in_the_deeper_ancestor(self, management_chain, make_request):
        alice, _bob, _carol = management_chain
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request("/", {"include": "owner"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "manager" not in serializer.data["owner"]

    def test_a_three_level_dotted_path_pulls_in_every_ancestor(self, management_chain, make_request):
        alice, bob, carol = management_chain
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request("/", {"include": "owner.manager.manager"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert serializer.data["owner"]["manager"]["manager"] == {"id": carol.id, "username": "carol"}

    def test_dotted_includes_work_through_a_many_true_field(self, management_chain, make_request):
        alice, bob, _carol = management_chain
        widget = Widget.objects.create(name="gear", count=1, owner=bob)
        request = make_request("/", {"include": "owner.reports,owner.reports.manager"})
        serializer = WidgetSerializer(widget, context={"request": request})
        [report] = serializer.data["owner"]["reports"]
        assert report["id"] == alice.id
        assert report["manager"] == {"id": bob.id, "username": "bob"}

    def test_a_many_true_field_is_excluded_by_default_like_any_other(self, management_chain, make_request):
        _alice, bob, _carol = management_chain
        widget = Widget.objects.create(name="gear", count=1, owner=bob)
        request = make_request("/", {"include": "owner"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "reports" not in serializer.data["owner"]


class TestOnlyFilter:
    def test_only_narrows_top_level_fields(self, widget, make_request):
        request = make_request("/", {"only": "name"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert set(serializer.data) == {"name"}

    def test_only_is_a_no_op_when_absent(self, widget, make_request):
        serializer = WidgetSerializer(widget, context={"request": make_request("/")})
        assert set(serializer.data) == {"id", "name", "count"}

    def test_comma_separated_only_values_are_split(self, widget, make_request):
        request = make_request("/", {"only": "name,count"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert set(serializer.data) == {"name", "count"}

    def test_only_works_without_any_relational_fields_declared(self, widget, make_request):
        request = make_request("/", {"only": "name"})
        serializer = PlainWidgetSerializer(widget, context={"request": request})
        assert set(serializer.data) == {"name"}

    def test_nested_only_alone_does_not_include_the_field(self, widget, make_request):
        request = make_request("/", {"only": "owner.username"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "owner" not in serializer.data

    def test_nested_only_together_with_include_narrows_the_nested_serializer(self, widget, make_request):
        request = make_request("/", {"include": "owner", "only": "owner.username"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert serializer.data["owner"] == {"username": "alice"}

    def test_only_stays_exhaustive_at_the_root_alongside_a_dotted_entry(self, widget, make_request):
        request = make_request("/", {"include": "owner", "only": "name,owner.username"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert set(serializer.data) == {"name", "owner"}
        assert serializer.data["owner"] == {"username": "alice"}

    def test_only_reaches_two_levels_deep_alongside_matching_includes(self, management_chain, make_request):
        alice, bob, _carol = management_chain
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request("/", {"include": "owner.manager", "only": "owner.manager.username"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert serializer.data["owner"]["manager"] == {"username": "bob"}


class TestExcludeFilter:
    def test_exclude_drops_a_top_level_field(self, widget, make_request):
        request = make_request("/", {"exclude": "count"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert set(serializer.data) == {"id", "name"}

    def test_exclude_is_a_no_op_when_absent(self, widget, make_request):
        serializer = WidgetSerializer(widget, context={"request": make_request("/")})
        assert set(serializer.data) == {"id", "name", "count"}

    def test_comma_separated_exclude_values_are_split(self, widget, make_request):
        request = make_request("/", {"exclude": "id,count"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert set(serializer.data) == {"name"}

    def test_exclude_wins_over_a_matching_include(self, widget, make_request):
        request = make_request("/", {"include": "owner", "exclude": "owner"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "owner" not in serializer.data

    def test_nested_exclude_alone_without_include_does_nothing(self, widget, make_request):
        request = make_request("/", {"exclude": "owner.username"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert "owner" not in serializer.data

    def test_nested_exclude_drops_a_field_from_the_included_nested_serializer(self, widget, make_request):
        request = make_request("/", {"include": "owner", "exclude": "owner.username"})
        serializer = WidgetSerializer(widget, context={"request": request})
        assert serializer.data["owner"] == {"id": widget.owner_id}


class TestSerializerMethodInclude:
    def test_a_dotted_include_reaches_into_an_ad_hoc_serializer_method_field(self, management_chain, make_request):
        alice, bob, _carol = management_chain
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request("/", {"include": "owner_detail.manager"})
        serializer = WidgetSerializerWithMethodField(widget, context={"request": request})
        assert serializer.data["owner_detail"]["manager"] == {"id": bob.id, "username": "bob"}

    def test_the_nested_field_is_excluded_by_default(self, management_chain, make_request):
        alice, _bob, _carol = management_chain
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request("/")
        serializer = WidgetSerializerWithMethodField(widget, context={"request": request})
        assert "manager" not in serializer.data["owner_detail"]

    def test_exclude_drops_a_field_from_the_ad_hoc_nested_serializer_without_needing_include(
        self, widget, make_request
    ):
        request = make_request("/", {"exclude": "owner_detail.username"})
        serializer = WidgetSerializerWithMethodField(widget, context={"request": request})
        assert serializer.data["owner_detail"] == {"id": widget.owner_id}

    def test_only_narrows_the_ad_hoc_nested_serializer_without_needing_include(self, widget, make_request):
        request = make_request("/", {"only": "owner_detail.username"})
        serializer = WidgetSerializerWithMethodField(widget, context={"request": request})
        assert serializer.data["owner_detail"] == {"username": "alice"}

    def test_a_none_return_passes_through_instead_of_crashing(self, widget, make_request):
        class NullableSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
            owner_detail = serializers.SerializerMethodField()

            class Meta:
                model = Widget
                fields = ["id", "name", "owner_detail"]

            @serializer_method_include
            def get_owner_detail(self, obj):
                return None

        request = make_request("/")
        serializer = NullableSerializer(widget, context={"request": request})
        assert serializer.data["owner_detail"] is None
