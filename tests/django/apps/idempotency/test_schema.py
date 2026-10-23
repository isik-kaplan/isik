"""What isik's AutoSchema documents of an IdempotencyMixin view - the key a generated client must send."""

import pytest
from django.test import override_settings
from drf_spectacular.generators import SchemaGenerator

from tests.django.apps.idempotency import urls


pytestmark = pytest.mark.django_db

KEY = {
    "in": "header",
    "name": "Idempotency-Key",
    "schema": {"type": "string", "format": "uuid"},
    "description": (
        "A UUID the client makes for this request and sends again on each retry of it - a retry answers with the "
        "first request's response rather than doing the work twice."
    ),
    "required": True,
}


@pytest.fixture
def schema():
    # Per test, not per module: mutation testing runs a mutant only against the tests that ran its code.
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
        return SchemaGenerator(patterns=urls.urlpatterns).get_schema(request=None, public=True)


def headers(schema, path, method):
    return {p["name"]: p for p in schema["paths"][path][method].get("parameters", []) if p["in"] == "header"}


def responses(schema, path, method):
    return schema["paths"][path][method]["responses"]


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/widgets/", "post"),
        ("/widgets/{id}/", "put"),
        ("/widgets/{id}/", "patch"),
        ("/widgets/{id}/", "delete"),
        ("/widgets/{id}/bump/", "post"),
        ("/create-widget/", "post"),
    ],
)
def test_every_covered_write_documents_the_key(schema, path, method):
    assert headers(schema, path, method)["Idempotency-Key"] == KEY
    assert set(responses(schema, path, method)) >= {"409", "422"}


def test_a_read_documents_none_of_it(schema):
    assert "Idempotency-Key" not in headers(schema, "/widgets/", "get")
    assert not set(responses(schema, "/widgets/", "get")) & {"409", "422"}


def test_an_exempt_action_documents_none_of_it(schema):
    assert "Idempotency-Key" not in headers(schema, "/widgets/preview/", "post")
    assert not set(responses(schema, "/widgets/preview/", "post")) & {"409", "422"}


def test_a_key_not_required_is_optional(schema):
    assert "required" not in headers(schema, "/optional-widgets/", "post")["Idempotency-Key"]


def test_the_bodies_name_their_codes(schema):
    components = schema["components"]["schemas"]

    assert responses(schema, "/widgets/", "post")["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/IdempotencyKeyInFlight"
    }
    assert components["IdempotencyKeyInFlight"]["properties"]["code"]["description"] == (
        "Always idempotency_key_in_flight."
    )
    assert responses(schema, "/widgets/", "post")["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/IdempotencyKeyReused"
    }
    assert components["IdempotencyKeyReused"]["properties"]["code"]["description"] == ("Always idempotency_key_reused.")
    for name in ("IdempotencyKeyInFlight", "IdempotencyKeyReused"):
        assert set(components[name]["properties"]) == set(components[name]["required"]) == {"detail", "code"}


def test_a_no_replay_actions_conflict_can_also_be_that(schema):
    conflict = responses(schema, "/widgets/issue_secret/", "post")["409"]["content"]["application/json"]["schema"]

    assert conflict == {"$ref": "#/components/schemas/IdempotencyKeyConflict"}
    assert set(schema["components"]["schemas"]["IdempotencyKeyConflict"]["properties"]) == {"detail", "code"}
    assert schema["components"]["schemas"]["IdempotencyKeyConflict"]["properties"]["code"]["description"] == (
        "One of idempotency_key_in_flight, idempotency_key_not_replayable."
    )


def test_each_response_is_documented_as_its_own_status():
    """A header a view documents for its 409 lands on the 409 isik adds - so each is built for its code."""
    from drf_spectacular.utils import OpenApiParameter, extend_schema
    from rest_framework.routers import SimpleRouter

    from tests.django.apps.idempotency.views import WidgetViewSet

    # Inside the settings: extend_schema() picks the schema class when it decorates.
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):

        class RetryingWidgetViewSet(WidgetViewSet):
            @extend_schema(parameters=[OpenApiParameter("Retry-After", int, OpenApiParameter.HEADER, response=[409])])
            def create(self, request, *args, **kwargs):
                return super().create(request, *args, **kwargs)

        router = SimpleRouter()
        router.register("retrying", RetryingWidgetViewSet, basename="retrying")
        generated = SchemaGenerator(patterns=router.urls).get_schema(request=None, public=True)

    created = responses(generated, "/retrying/", "post")
    assert "Retry-After" in created["409"]["headers"]
    assert "headers" not in created["422"]
