"""The viewsets' models found with no request - by isik's checks, and by schema generation."""

import pytest
from django.test import override_settings
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from rest_framework.generics import GenericAPIView
from rest_framework.pagination import PageNumberPagination

from isik.django.apps.notifications.audience.models import InboxItem as InboxItemModel
from isik.django.apps.notifications.drf import (
    DeliveryViewSet,
    EndpointViewSet,
    InboxViewSet,
    SubscriptionViewSet,
    SwappedModel,
)
from isik.django.drf.viewsets.preconditions import precondition_problems
from tests.django.apps.notifications.api import urls
from tests.testapp.models import Parcel


pytestmark = pytest.mark.django_db

VIEWSETS = (InboxViewSet, SubscriptionViewSet, EndpointViewSet, DeliveryViewSet)
SETTINGS = (
    "ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL",
    "ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL",
    "ISIK_NOTIFICATIONS_ENDPOINT_MODEL",
    "ISIK_NOTIFICATIONS_DELIVERY_MODEL",
)


def test_each_finds_the_model_it_serves():
    from isik.django.apps.notifications.audience.models import Endpoint, InboxItem, Subscription
    from isik.django.apps.notifications.models import Delivery

    assert [view.get_precondition_model() for view in VIEWSETS] == [InboxItem, Subscription, Endpoint, Delivery]


def test_each_finds_the_model_a_project_swapped_in(settings):
    for setting in SETTINGS:
        setattr(settings, setting, "testapp.Parcel")

    assert [view.get_precondition_model() for view in VIEWSETS] == [Parcel] * 4
    assert [view().model for view in VIEWSETS] == [Parcel] * 4


def test_the_check_finds_every_model():
    assert not [problem for problem in precondition_problems(urls) if "no model isik can find" in problem]


def schema():
    rest_framework = {"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}
    with override_settings(REST_FRAMEWORK=rest_framework):
        return SchemaGenerator(patterns=urls.urlpatterns).get_schema(request=None, public=True)


def test_the_schema_is_generated_with_no_warning():
    GENERATOR_STATS.reset()

    schema()

    assert (dict(GENERATOR_STATS._warn_cache), dict(GENERATOR_STATS._error_cache)) == ({}, {})


def test_the_schema_types_each_id():
    paths = schema()["paths"]

    def id_of(path):
        [parameter] = [parameter for parameter in paths[path]["get"]["parameters"] if parameter["name"] == "id"]
        return {key: parameter["schema"][key] for key in ("type", "format") if key in parameter["schema"]}

    uuid = {"type": "string", "format": "uuid"}
    assert id_of("/unscoped-deliveries/{id}/") == uuid
    assert id_of("/inbox/{id}/") == uuid
    assert id_of("/unscoped-endpoints/{id}/") == uuid


def test_a_swapped_model_is_read_when_asked_for():
    models = iter([Parcel, InboxItemModel])
    swapped = SwappedModel(lambda: next(models))

    assert (swapped.__get__(None, object), swapped.__get__(object(), object)) == (Parcel, InboxItemModel)


def test_what_the_schema_tells_api_callers_names_none_of_isiks_internals():
    described = []

    def collect(node):
        if isinstance(node, dict):
            described.extend(value for key, value in node.items() if key == "description" and isinstance(value, str))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema())

    internal = ("owner_field", "get_queryset", "perform_create", "retry_delivery", "subscribe()", "Subclass")
    assert [text for text in described if any(word in text for word in internal)] == []
    assert "Your notifications, newest first. Add `?unread=true` for the unread ones alone." in described
    assert "Sends to an endpoint again after it was parked for failing too often." in described


def test_each_operation_documents_what_it_does():
    paths = schema()["paths"]

    def body(path, method):
        return paths[path][method].get("requestBody")

    def answer(path, method, code="200"):
        return paths[path][method]["responses"][code]["content"]["application/json"]["schema"]

    unread = [parameter for parameter in paths["/inbox/"]["get"]["parameters"] if parameter["name"] == "unread"]
    assert [parameter["schema"] for parameter in unread] == [{"type": "boolean"}]
    for path in (
        "/inbox/{id}/read/",
        "/inbox/read-all/",
        "/unscoped-endpoints/{id}/rotate-secret/",
        "/unscoped-endpoints/{id}/finish-rotation/",
        "/unscoped-endpoints/{id}/unpark/",
        "/unscoped-deliveries/{id}/retry/",
    ):
        assert body(path, "post") is None, path
    assert answer("/inbox/read-all/", "post") == {"$ref": "#/components/schemas/Marked"}
    assert answer("/kinds/", "get") == {"type": "array", "items": {"$ref": "#/components/schemas/NoticeKind"}}
    assert answer("/subscriptions/", "post", "201") == {"$ref": "#/components/schemas/Subscription"}
    assert answer("/unscoped-endpoints/{id}/unpark/", "post") == {"$ref": "#/components/schemas/Endpoint"}
    assert answer("/unscoped-deliveries/{id}/retry/", "post") == {"$ref": "#/components/schemas/Delivery"}


@pytest.mark.parametrize("paginated", [False, True])
def test_what_answers_a_list_or_one_is_documented_so_under_any_paginator(paginated, monkeypatch):
    # DEFAULT_PAGINATION_CLASS is read once, when GenericAPIView is defined - so a project's lands there.
    if paginated:
        monkeypatch.setattr(GenericAPIView, "pagination_class", PageNumberPagination)
    paths = schema()["paths"]

    def answer(path, method):
        return paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]

    assert answer("/kinds/", "get")["type"] == "array"
    assert answer("/inbox/read-all/", "post") == {"$ref": "#/components/schemas/Marked"}
    for path in (
        "/inbox/{id}/read/",
        "/unscoped-endpoints/{id}/rotate-secret/",
        "/unscoped-endpoints/{id}/finish-rotation/",
        "/unscoped-endpoints/{id}/unpark/",
        "/unscoped-deliveries/{id}/retry/",
    ):
        assert set(answer(path, "post")) == {"$ref"}, path


def test_every_write_is_drfs_own_or_declared():
    # A create of its own would leave drf-spectacular inferring its body from a method it can't read - so the
    # subscription view's create only names the act, and DRF's mixin does it.
    assert "perform_create" in vars(SubscriptionViewSet)
    operation = schema()["paths"]["/subscriptions/"]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]

    assert body == {"$ref": "#/components/schemas/Subscription"}
    assert operation["description"].startswith("Sets your preference for a kind of notification")
