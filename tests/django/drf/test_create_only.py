import pytest
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
