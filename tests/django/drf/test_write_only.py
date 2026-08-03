import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from isik.django.drf.serializers.write_only import WriteOnlyFieldsMixin
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(WriteOnlyFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        write_only_fields = ["count"]


class TestWriteOnlyFieldsMixin:
    def test_write_only_field_is_settable(self):
        serializer = WidgetSerializer(data={"name": "bolt", "count": 1})
        serializer.is_valid(raise_exception=True)
        widget = serializer.save()
        assert widget.count == 1

    def test_write_only_field_never_appears_in_the_serialized_output(self):
        widget = Widget.objects.create(name="bolt", count=1)
        assert "count" not in WidgetSerializer(widget).data

    def test_overlapping_write_only_and_create_only_fields_raise_at_class_definition_time(self):
        with pytest.raises(ImproperlyConfigured, match="count"):

            class ConflictingSerializer(WriteOnlyFieldsMixin, serializers.ModelSerializer):
                class Meta:
                    model = Widget
                    fields = ["id", "name", "count"]
                    create_only_fields = ["count"]
                    write_only_fields = ["count"]

    def test_no_meta_at_all_does_not_crash(self):
        class NoMetaSerializer(WriteOnlyFieldsMixin, serializers.Serializer):
            name = serializers.CharField()

        NoMetaSerializer()  # just needs to not raise at class-definition time
