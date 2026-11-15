"""x-sync on each synced model's component - the protocol's models.json, from isik's own schema."""

import dataclasses
import json
from pathlib import Path

import pytest
from drf_spectacular.generators import SchemaGenerator

from isik.django.apps.sync import declarations
from isik.django.apps.sync.spectacular import add_sync_extensions, column_type, indexed_of, x_sync
from tests.testapp.models import SyncBoard, SyncTask


MODELS = json.loads((Path(__file__).resolve().parents[3] / "protocol" / "sync" / "v1" / "models.json").read_text())


def generated(urlconf="tests.testapp.sync_urls"):
    generator = SchemaGenerator(urlconf=urlconf)
    schema = generator.get_schema(request=None, public=True)
    return add_sync_extensions(schema, generator, None, True)["components"]["schemas"]


@pytest.mark.parametrize("name", sorted(MODELS))
def test_each_synced_component_says_what_the_protocol_s_models_say(name):
    found = next(schema["x-sync"] for schema in generated().values() if schema.get("x-sync", {}).get("model") == name)

    assert {**found, "hash": MODELS[name]["hash"]} == MODELS[name]
    assert len(found["hash"]) == 64


def test_the_hash_moves_with_what_it_covers():
    a = x_sync(SyncTask.synced, "/a/", {"id": "uuid"})
    b = x_sync(SyncTask.synced, "/b/", {"id": "uuid"})

    assert a["hash"] != b["hash"]
    assert a["hash"] == x_sync(SyncTask.synced, "/a/", {"id": "uuid"})["hash"]


def test_a_relation_s_column_is_json():
    board = next(schema for schema in generated().values() if schema.get("x-sync", {}).get("model") == "boards")

    assert board["x-sync"]["columns"] == {"id": "uuid", "owner": "uuid", "teams": "json", "color": "string"}
    assert SyncBoard.synced.name == "boards"


def test_a_model_routed_nowhere_gets_none():
    schemas = generated("tests.testapp.sync_urls_without_tasks")

    assert [schema["x-sync"]["model"] for schema in schemas.values() if "x-sync" in schema] == ["projects"]


def test_a_model_routed_with_no_retrieve_gets_none():
    schemas = generated("tests.testapp.sync_urls_creating_only")

    assert [schema for schema in schemas.values() if "x-sync" in schema] == []


def test_a_document_with_no_synced_model_routed_is_left_as_it_is():
    generator = type("Generator", (), {"endpoints": []})()

    assert add_sync_extensions({"paths": {}}, generator, None, True) == {"paths": {}}


COMPONENTS = {
    "StateEnum": {"enum": ["a", "b"], "type": "string"},
    "NullEnum": {"enum": [None]},
    "CountEnum": {"enum": [1, 2], "type": "integer"},
}


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string", "format": "uuid"}, "uuid"),
        ({"type": "string", "format": "date-time"}, "datetime"),
        ({"type": "string", "format": "date"}, "date"),
        ({"type": "string", "format": "email"}, "string"),
        ({"type": "integer", "format": "int64"}, "integer"),
        ({"type": "number", "format": "double"}, "number"),
        ({"type": "boolean"}, "boolean"),
        ({"type": "object"}, "json"),
        ({"type": "array", "items": {}}, "json"),
        ({}, "json"),
        ({"type": ["string", "null"]}, "string"),
        ({"type": ["null", "integer"]}, "integer"),
        ({"type": ["null"]}, "json"),
        ({"$ref": "#/components/schemas/StateEnum"}, "string"),
        ({"allOf": [{"$ref": "#/components/schemas/StateEnum"}]}, "string"),
        ({"oneOf": [{"$ref": "#/components/schemas/StateEnum"}, {"$ref": "#/components/schemas/NullEnum"}]}, "string"),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, "integer"),
        ({"oneOf": [{"$ref": "#/components/schemas/StateEnum"}, {"$ref": "#/components/schemas/CountEnum"}]}, "json"),
    ],
)
def test_a_column_s_type(schema, expected):
    assert column_type(schema, COMPONENTS) == expected


def test_indexed_as_declared_or_off_the_model(monkeypatch):
    columns = {"id": "uuid", "owner": "uuid", "title": "string", "done": "boolean"}
    declared = dataclasses.replace(SyncTask.synced, indexed=(("title",),))

    assert indexed_of(declared, columns) == [["title"]]
    assert indexed_of(SyncTask.synced, columns) == [["owner"], ["done", "title"]]
    assert declarations
