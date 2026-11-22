"""A client-made id's schema: required in a response, optional in a request."""

import pytest
from django.test import override_settings
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from rest_framework import serializers, viewsets
from rest_framework.routers import SimpleRouter

from isik.django.apps.sync.drf import ClientPrimaryKeyMixin
from tests.testapp.models import SyncProject
from tests.testapp.sync_views import ProjectViewSet


class IdOnlySerializer(ClientPrimaryKeyMixin, serializers.ModelSerializer):
    class Meta:
        model = SyncProject
        fields = ["id"]
        ref_name = "IdOnly"


class NoIdSerializer(ClientPrimaryKeyMixin, serializers.ModelSerializer):
    class Meta:
        model = SyncProject
        fields = ["owner", "name"]
        ref_name = "NoId"


def components(split, **viewsets):
    router = SimpleRouter()
    for endpoint, viewset in viewsets.items():
        router.register(endpoint, viewset, basename=endpoint)
    rest_framework = {"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}
    with override_settings(REST_FRAMEWORK=rest_framework), patched_settings({"COMPONENT_SPLIT_REQUEST": split}):
        return SchemaGenerator(patterns=router.urls).get_schema(request=None, public=True)["components"]["schemas"]


def test_required_in_the_response_and_optional_in_the_request():
    schemas = components(True, projects=ProjectViewSet)

    assert schemas["Project"]["required"] == ["id", "name", "owner"]
    assert schemas["ProjectRequest"]["required"] == ["name", "owner"]


def test_one_component_for_both_keeps_it_optional():
    assert components(False, projects=ProjectViewSet)["Project"]["required"] == ["name", "owner"]


@pytest.mark.parametrize(("serializer", "required"), [(IdOnlySerializer, ["id"]), (NoIdSerializer, ["name", "owner"])])
def test_a_serializer_with_no_other_field_required_or_no_id(serializer, required):
    viewset = type(
        "Rows", (viewsets.ModelViewSet,), {"serializer_class": serializer, "queryset": SyncProject.objects.none()}
    )

    assert components(True, rows=viewset)[serializer.Meta.ref_name]["required"] == required
