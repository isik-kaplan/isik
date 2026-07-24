import pytest
from rest_framework import serializers

from isik.django.drf.utils.related_count import ModelRelatedCountField
from tests.testapp.models import Comment, Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    comment_count = ModelRelatedCountField(related_name="comments")

    class Meta:
        model = Widget
        fields = ["id", "name", "comment_count"]


class TestModelRelatedCountField:
    def test_reports_the_count_of_the_related_manager(self):
        widget = Widget.objects.create(name="bolt", count=1)
        Comment.objects.create(body="one", widget=widget)
        Comment.objects.create(body="two", widget=widget)
        assert WidgetSerializer(widget).data["comment_count"] == 2

    def test_zero_when_there_are_no_related_objects(self):
        widget = Widget.objects.create(name="bolt", count=1)
        assert WidgetSerializer(widget).data["comment_count"] == 0

    def test_is_read_only(self):
        field = WidgetSerializer().fields["comment_count"]
        assert field.read_only is True
