import pytest
from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.drf.serializers.conditional_serializer import ConditionalSerializerMixin
from isik.django.drf.utils.lazy_relations import LazyGenericRelatedField, LazyPrimaryKeyRelatedField
from tests.testapp.models import Note, Tag, Widget


pytestmark = pytest.mark.django_db


class WidgetOwnerSerializer(serializers.ModelSerializer):
    owner = LazyPrimaryKeyRelatedField(queryset_func=lambda: get_user_model().objects.all())

    class Meta:
        model = Widget
        fields = ["id", "name", "owner"]


class TestLazyPrimaryKeyRelatedField:
    def test_get_queryset_calls_the_func_lazily(self):
        calls = []

        def queryset_func():
            calls.append(1)
            return Widget.objects.all()

        field = LazyPrimaryKeyRelatedField(queryset_func=queryset_func)
        assert calls == []
        field.get_queryset()
        assert calls == [1]

    def test_resolves_a_valid_pk_through_a_real_serializer(self, django_user_model):
        user = django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = WidgetOwnerSerializer(widget, data={"name": "bolt", "owner": user.id}, partial=True)
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] == user

    def test_rejects_a_pk_not_in_the_lazily_resolved_queryset(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = WidgetOwnerSerializer(widget, data={"name": "bolt", "owner": 999999}, partial=True)
        assert not serializer.is_valid()
        assert "owner" in serializer.errors


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "label"]


class TestLazyGenericRelatedFieldRepresentation:
    def test_picks_the_serializer_registered_for_the_instances_exact_model(self):
        widget = Widget.objects.create(name="bolt", count=1)
        field = LazyGenericRelatedField(lambda: {Widget: WidgetSerializer(), Tag: TagSerializer()})
        assert field.to_representation(widget) == {"id": str(widget.id), "name": "bolt"}

    def test_falls_back_to_a_registered_superclass(self):
        class Parent: ...

        class Child(Parent): ...

        class ParentSerializer(serializers.Serializer):
            kind = serializers.SerializerMethodField()

            def get_kind(self, obj):
                return "parent"

        field = LazyGenericRelatedField(lambda: {Parent: ParentSerializer()})
        assert field.to_representation(Child()) == {"kind": "parent"}

    def test_raises_when_no_serializer_is_registered_for_the_model(self):
        widget = Widget.objects.create(name="bolt", count=1)
        field = LazyGenericRelatedField(lambda: {Tag: TagSerializer()})
        with pytest.raises(ValidationError) as exc_info:
            field.to_representation(widget)
        assert exc_info.value.detail[0].code == "no_model_match"

    def test_calls_serializers_func_fresh_each_access(self):
        calls = []

        def serializers_func():
            calls.append(1)
            return {Widget: WidgetSerializer()}

        field = LazyGenericRelatedField(serializers_func)
        widget = Widget.objects.create(name="bolt", count=1)
        field.to_representation(widget)
        field.to_representation(widget)
        assert calls == [1, 1]


class TestLazyGenericRelatedFieldInternalValue:
    def test_resolves_when_exactly_one_serializer_matches(self):
        field = LazyGenericRelatedField(lambda: {Widget: WidgetSerializer(), Tag: TagSerializer()})
        assert field.to_internal_value({"label": "urgent"}) == {"label": "urgent"}

    def test_raises_when_no_serializer_matches(self):
        field = LazyGenericRelatedField(lambda: {Tag: TagSerializer()})
        with pytest.raises(ValidationError) as exc_info:
            field.to_internal_value({"totally": "unrelated"})
        assert exc_info.value.detail[0].code == "no_data_match"

    def test_raises_when_more_than_one_serializer_matches(self):
        field = LazyGenericRelatedField(lambda: {Widget: WidgetSerializer(), Tag: TagSerializer()})
        with pytest.raises(ValidationError) as exc_info:
            # "name" alone validates against both WidgetSerializer and TagSerializer's optional fields.
            field.to_internal_value({"name": "bolt", "label": "urgent"})
        assert exc_info.value.detail[0].code == "ambiguous_data"


class ConditionalWidgetSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class ConditionalTagSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "label"]


class NoteSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    """
    Ties AutoGenericForeignKey (Note.target, model side) together with
    LazyGenericRelatedField (serializer side) and ConditionalSerializerMixin, the
    realistic combination this field exists for.
    """

    target = LazyGenericRelatedField(lambda: {Widget: ConditionalWidgetSerializer(), Tag: ConditionalTagSerializer()})

    class Meta:
        model = Note
        fields = ["id", "body", "target"]


@pytest.fixture
def make_request():
    factory = APIRequestFactory()
    return lambda query_string="": Request(factory.get(f"/?{query_string}"))


class TestLazyGenericRelatedFieldWithConditionalSerializerMixin:
    def test_target_itself_can_be_dropped_by_top_level_exclude(self, make_request):
        widget = Widget.objects.create(name="bolt", count=1)
        note = Note.objects.create(body="hi", target=widget)
        request = make_request("exclude=target")
        data = NoteSerializer(note, context={"request": request}).data
        assert data == {"id": str(note.id), "body": "hi"}

    def test_only_narrows_fields_inside_the_nested_polymorphic_serializer(self, make_request):
        widget = Widget.objects.create(name="bolt", count=1)
        note = Note.objects.create(body="hi", target=widget)
        request = make_request("only=target.name")
        data = NoteSerializer(note, context={"request": request}).data
        assert data["target"] == {"name": "bolt"}

    def test_exclude_drops_a_field_inside_the_nested_polymorphic_serializer(self, make_request):
        widget = Widget.objects.create(name="bolt", count=1)
        note = Note.objects.create(body="hi", target=widget)
        request = make_request("exclude=target.name")
        data = NoteSerializer(note, context={"request": request}).data
        assert data["target"] == {"id": str(widget.id), "count": 1}

    def test_the_nested_filter_applies_regardless_of_which_model_is_registered(self, make_request):
        # Note.target is limited (at the model layer, enforced by full_clean) to Widget - so this
        # builds an unsaved instance to exercise the serializer's own dict-based dispatch against
        # a second registered model, independent of that model-level constraint.
        tag = Tag.objects.create(label="urgent")
        note = Note(body="hi")
        note.target = tag
        request = make_request("only=target.label")
        data = NoteSerializer(note, context={"request": request}).data
        assert data["target"] == {"label": "urgent"}
