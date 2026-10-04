import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from isik.django.drf.serializers.create_only import CreateOnlyFieldsMixin
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(CreateOnlyFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        create_only_fields = ["name"]


class WidgetSerializerWithExtraKwargs(CreateOnlyFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        create_only_fields = ["name"]
        extra_kwargs = {"name": {"help_text": "Widget name"}}


class WidgetSerializerWithMultipleCreateOnlyFields(CreateOnlyFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        create_only_fields = ["name", "count"]


class TestCreateOnlyFieldsMixin:
    def test_create_only_field_is_writable_on_create(self):
        serializer = WidgetSerializer(data={"name": "bolt", "count": 1})
        serializer.is_valid(raise_exception=True)
        widget = serializer.save()
        assert widget.name == "bolt"

    def test_create_only_field_is_forced_read_only_on_update(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = WidgetSerializer(widget, data={"name": "renamed", "count": 2})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        assert updated.name == "bolt"
        assert updated.count == 2

    def test_other_extra_kwargs_on_a_create_only_field_survive_the_forced_read_only(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = WidgetSerializerWithExtraKwargs(widget, data={"name": "renamed", "count": 2})
        assert serializer.fields["name"].help_text == "Widget name"
        assert serializer.fields["name"].read_only is True
        assert serializer.fields["name"].required is False

    def test_multiple_create_only_fields_are_all_forced_read_only_on_update(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = WidgetSerializerWithMultipleCreateOnlyFields(widget, data={"name": "renamed", "count": 99})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        assert updated.name == "bolt"
        assert updated.count == 1

    def test_create_only_field_is_forced_read_only_on_a_partial_update_too(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = WidgetSerializer(widget, data={"count": 2}, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        assert updated.name == "bolt"
        assert updated.count == 2


class RefusingWidgetSerializer(CreateOnlyFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count", "owner"]
        create_only_fields = ["name", "owner"]
        create_only_changes = "refuse"


class TestRefusingAChange:
    """`create_only_changes = "refuse"`: an update moving a create-only field is told so, by name."""

    def test_a_changed_value_is_refused_by_name(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = RefusingWidgetSerializer(widget, data={"name": "renamed", "count": 2})

        assert serializer.is_valid() is False
        assert serializer.errors == {"name": ["This field can't be changed once it's set."]}
        assert serializer.errors["name"][0].code == "create_only"
        widget.refresh_from_db()
        assert (widget.name, widget.count) == ("bolt", 1)

    def test_every_changed_field_is_named(self, django_user_model):
        owner = django_user_model.objects.create(username="alice", email="alice@example.com")
        other = django_user_model.objects.create(username="bob", email="bob@example.com")
        widget = Widget.objects.create(name="bolt", owner=owner)

        serializer = RefusingWidgetSerializer(widget, data={"name": "renamed", "owner": other.pk}, partial=True)

        assert serializer.is_valid() is False
        assert set(serializer.errors) == {"name", "owner"}

    def test_the_same_value_sent_back_passes(self, django_user_model):
        owner = django_user_model.objects.create(username="alice", email="alice@example.com")
        widget = Widget.objects.create(name="bolt", count=1, owner=owner)
        serializer = RefusingWidgetSerializer(widget, data={"name": "bolt", "owner": owner.pk, "count": 2})

        serializer.is_valid(raise_exception=True)

        assert serializer.save().count == 2

    def test_left_out_it_isnt_required(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = RefusingWidgetSerializer(widget, data={"count": 2})

        serializer.is_valid(raise_exception=True)

        assert serializer.save().name == "bolt"
        assert serializer.fields["name"].read_only is False
        assert serializer.fields["name"].required is False

    def test_on_create_it_is_an_ordinary_field(self):
        serializer = RefusingWidgetSerializer(data={"name": "bolt", "count": 1})

        serializer.is_valid(raise_exception=True)

        assert serializer.save().name == "bolt"
        assert serializer.fields["name"].required is True

    def test_ignoring_is_the_default_and_can_be_said(self):
        class Ignoring(CreateOnlyFieldsMixin, serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name"]
                create_only_fields = ["name"]
                create_only_changes = "ignore"

        widget = Widget.objects.create(name="bolt")
        for serializer_cls in (Ignoring, WidgetSerializer):
            serializer = serializer_cls(widget, data={"name": "renamed"}, partial=True)
            serializer.is_valid(raise_exception=True)
            assert serializer.save().name == "bolt"

    def test_anything_else_is_a_misconfiguration(self):
        class Unsure(CreateOnlyFieldsMixin, serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name"]
                create_only_fields = ["name"]
                create_only_changes = "warn"

        widget = Widget.objects.create(name="bolt")
        with pytest.raises(ImproperlyConfigured) as raised:
            Unsure(widget, data={"name": "x"}).is_valid()

        assert str(raised.value) == "Unsure.Meta.create_only_changes is 'warn' - it takes 'ignore' or 'refuse'."

    def test_a_serializer_without_create_only_fields_is_left_alone(self):
        class Plain(CreateOnlyFieldsMixin, serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name"]
                create_only_changes = "refuse"

        widget = Widget.objects.create(name="bolt")
        serializer = Plain(widget, data={"name": "renamed"})

        serializer.is_valid(raise_exception=True)

        assert serializer.save().name == "renamed"
