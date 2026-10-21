"""What isik's AutoSchema documents of a PreconditionMixin view - so generated clients know its headers."""

import pytest
from django.test import override_settings
from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
from drf_spectacular.generators import SchemaGenerator
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import ModelViewSet

from isik.django.drf.spectacular import AutoSchema
from isik.django.drf.viewsets import PreconditionMixin
from tests.django.drf.preconditions import urls
from tests.django.drf.preconditions.views import WidgetSerializer
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db

ETAG = {
    "schema": {"type": "string"},
    "description": "The version of the object this response shows. Send it back as it is, in If-Match.",
}


@pytest.fixture
def schema():
    reset_generator_stats()
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
        generated = SchemaGenerator(patterns=urls.urlpatterns).get_schema(request=None, public=True)
    assert not GENERATOR_STATS._warn_cache
    assert not GENERATOR_STATS._error_cache
    return generated


def operation(schema, path, method):
    return schema["paths"][path][method]


def headers(op):
    return {p["name"]: p for p in op.get("parameters", []) if p["in"] == "header"}


class TestAWrite:
    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/widgets/{id}/", "put"),
            ("/widgets/{id}/", "patch"),
            ("/widgets/{id}/", "delete"),
            ("/widgets/{id}/bump/", "post"),
            ("/widget/{id}/", "patch"),
        ],
    )
    def test_requires_if_match_and_documents_412_and_428(self, schema, path, method):
        op = operation(schema, path, method)

        assert headers(op)["If-Match"] == {
            "in": "header",
            "name": "If-Match",
            "schema": {"type": "string"},
            "description": "The ETag of the version this request was made against, or * for whatever is current.",
            "required": True,
        }
        assert {"412", "428"} <= set(op["responses"])
        assert op["responses"]["412"]["headers"] == {"ETag": ETAG}
        assert "headers" not in op["responses"]["428"]

    def test_its_412_shows_current_as_retrieve_does(self, schema):
        op = operation(schema, "/widgets/{id}/", "patch")
        failed = op["responses"]["412"]["content"]["application/json"]["schema"]

        assert failed == {"$ref": "#/components/schemas/WidgetPreconditionFailed"}
        assert schema["components"]["schemas"]["WidgetPreconditionFailed"] == {
            "type": "object",
            "description": "A 412's body: the object as it is now, in `current`.",
            "properties": {
                "detail": {"type": "string"},
                "code": {"type": "string", "description": "Always precondition_failed."},
                "current": {"$ref": "#/components/schemas/Widget"},
            },
            "required": ["code", "current", "detail"],
        }

    def test_its_428_has_a_code(self, schema):
        op = operation(schema, "/widgets/{id}/", "patch")

        assert op["responses"]["428"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/PreconditionRequired"
        }
        assert schema["components"]["schemas"]["PreconditionRequired"]["properties"] == {
            "detail": {"type": "string"},
            "code": {"type": "string", "description": "Always precondition_required."},
        }

    def test_its_success_carries_the_new_etag(self, schema):
        assert operation(schema, "/widgets/{id}/", "patch")["responses"]["200"]["headers"] == {"ETag": ETAG}

    def test_a_delete_answers_with_no_etag(self, schema):
        assert "headers" not in operation(schema, "/widgets/{id}/", "delete")["responses"]["204"]

    def test_an_exempt_action_documents_none_of_it(self, schema):
        op = operation(schema, "/widgets/{id}/rename_freely/", "post")

        assert "If-Match" not in headers(op)
        assert not {"412", "428"} & set(op["responses"])


class TestARead:
    def test_retrieve_documents_if_none_match_the_304_and_the_etag(self, schema):
        op = operation(schema, "/widgets/{id}/", "get")

        assert headers(op)["If-None-Match"] == {
            "in": "header",
            "name": "If-None-Match",
            "schema": {"type": "string"},
            "description": "The ETag of the version already held - a 304 with no body if it's still current.",
        }
        assert op["responses"]["304"] == {
            "headers": {"ETag": ETAG},
            "description": "The version If-None-Match named is still current.",
        }
        assert op["responses"]["200"]["headers"] == {"ETag": ETAG}
        assert not {"412", "428"} & set(op["responses"])

    def test_a_view_that_isnt_a_viewset_documents_it_on_its_get(self, schema):
        assert "304" in operation(schema, "/widget/{id}/", "get")["responses"]

    @pytest.mark.parametrize(("path", "method"), [("/widgets/", "get"), ("/widgets/{id}/peek/", "get")])
    def test_another_read_documents_none_of_it(self, schema, path, method):
        op = operation(schema, path, method)

        assert headers(op) == {}
        assert set(op["responses"]) == {"200"}
        assert "headers" not in op["responses"]["200"]


class TestACreate:
    @pytest.mark.parametrize("path", ["/widgets/", "/widget/"])
    def test_documents_its_etag_and_no_condition(self, schema, path):
        op = operation(schema, path, "post")

        assert headers(op) == {}
        assert op["responses"]["201"]["headers"] == {"ETag": ETAG}
        assert set(op["responses"]) == {"201"}

    def test_a_post_to_no_one_object_documents_none_of_it(self, schema):
        op = operation(schema, "/widgets/make_two/", "post")

        assert headers(op) == {}
        assert "headers" not in op["responses"]["200"]


class LeavesOutPatches(AutoSchema):
    def is_excluded(self):
        return self.method == "PATCH"


class ExcludedWidgetViewSet(PreconditionMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    schema = LeavesOutPatches()


def generate(patterns):
    reset_generator_stats()
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
        generated = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    assert not GENERATOR_STATS._warn_cache
    assert not GENERATOR_STATS._error_cache
    return generated


def test_an_operation_left_out_of_the_schema_stays_out():
    router = SimpleRouter()
    router.register("excluded", ExcludedWidgetViewSet, basename="excluded")

    paths = generate(router.urls)["paths"]

    assert "patch" not in paths["/excluded/{id}/"]
    assert "412" in paths["/excluded/{id}/"]["put"]["responses"]


def test_each_generation_builds_its_own_bodies_once():
    first, second = generate(urls.urlpatterns), generate(urls.urlpatterns)

    assert (
        first["components"]["schemas"]["WidgetPreconditionFailed"]
        == second["components"]["schemas"]["WidgetPreconditionFailed"]
    )
