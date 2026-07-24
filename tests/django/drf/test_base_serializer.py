import pytest
from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.drf.serializers.base import BaseModelSerializer
from isik.django.drf.serializers.conditional_serializer import relational_serializer
from isik.django.drf.serializers.registry import ModelSerializerRegistryMixin
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(BaseModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        create_only_fields = ["name"]


class OwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username"]


class WidgetSerializerWithOwnerInclude(BaseModelSerializer):
    # Widget is already registered to WidgetSerializer above - this is an intentional second
    # serializer for it, just to exercise the composed mixin stack's ?include=/?only=/?exclude=
    # handling end-to-end.
    exempt_from_registry = True

    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        relational_fields = {"owner": relational_serializer(OwnerSerializer)}


class TestBaseModelSerializer:
    def test_registers_itself_in_the_model_registry(self):
        assert ModelSerializerRegistryMixin.get_for_model(Widget) is WidgetSerializer

    def test_create_only_fields_still_works_through_the_composition(self):
        widget = Widget.objects.create(name="bolt", count=1)
        serializer = WidgetSerializer(widget, data={"name": "renamed", "count": 2})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        assert updated.name == "bolt"
        assert updated.count == 2

    def test_current_request_and_current_user_helpers_are_available(self):
        serializer = WidgetSerializer()
        assert serializer.current_request() is None
        assert serializer.current_user() is None

    def test_relational_fields_defaults_to_an_empty_dict(self):
        assert WidgetSerializer.Meta.relational_fields == {}

    def test_include_only_and_exclude_query_filtering_work_through_the_full_composition(self, django_user_model):
        owner = django_user_model.objects.create_user(username="alice", password="password")
        widget = Widget.objects.create(name="bolt", count=1, owner=owner)
        request = Request(APIRequestFactory().get("/", {"include": "owner", "only": "name,owner", "exclude": "count"}))
        serializer = WidgetSerializerWithOwnerInclude(widget, context={"request": request})
        assert serializer.data == {"name": "bolt", "owner": {"id": owner.id, "username": "alice"}}
