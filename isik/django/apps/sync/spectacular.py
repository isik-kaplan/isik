"""
`x-sync` on each synced model's component schema - everything a client needs to keep a local table of the
model: the name the feed calls it, its REST collection path, its key, its scope, its columns and their types,
the column lists to index it by, and a hash over all of it that changes when any of it does.

    SPECTACULAR_SETTINGS = {
        "POSTPROCESSING_HOOKS": [
            "drf_spectacular.hooks.postprocess_schema_enums",
            "isik.django.apps.sync.spectacular.add_sync_extensions",
        ],
    }
"""

import hashlib
import json
import re

from isik.django.apps.sync.declarations import synced_models
from isik.django.apps.sync.push import JSON, route
from isik.django.apps.sync.reading import viewset_of


PROTOCOL = 1
COMPONENT = "#/components/schemas/"
# {(OpenAPI type, format): the column type the protocol names}, a format of None for any other.
COLUMN_TYPES = {
    ("string", "uuid"): "uuid",
    ("string", "date-time"): "datetime",
    ("string", "date"): "date",
    ("string", None): "string",
    ("integer", None): "integer",
    ("number", None): "number",
    ("boolean", None): "boolean",
}


def _resolved(schema, components):
    while "$ref" in schema:
        schema = components[schema["$ref"].removeprefix(COMPONENT)]
    return schema


def _is_null(schema):
    return schema.get("type") == "null" or schema.get("enum") == [None]


def column_type(schema, components):
    """The protocol's type of a column its OpenAPI `schema` describes - `json` for anything else."""
    schema = _resolved(schema, components)
    for key in ("allOf", "oneOf", "anyOf"):
        if key in schema:
            types = {column_type(part, components) for part in schema[key] if not _is_null(_resolved(part, components))}
            return types.pop() if len(types) == 1 else "json"
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((each for each in kind if each != "null"), None)
    return COLUMN_TYPES.get((kind, schema.get("format")), COLUMN_TYPES.get((kind, None), "json"))


def indexed_of(synced, columns):
    """The column lists to index a synced model's copy by: `syncs(indexed=)`'s, else its keys' and indexes'."""
    if synced.indexed is not None:
        return [list(each) for each in synced.indexed]
    meta = synced.model._meta
    found = [[field.name] for field in meta.concrete_fields if field.db_index and not field.primary_key]
    found += [list(index.fields) for index in meta.indexes if index.fields]
    return [each for each in found if all(name in columns for name in each)]


def x_sync(synced, path, columns):
    """A synced model's `x-sync`, given its collection `path` and `{column: protocol type}`."""
    described = {
        "protocol": PROTOCOL,
        "model": synced.name,
        "path": path,
        "primary_key": synced.model._meta.pk.name,
        "scope": {"kind": synced.kind, "field": synced.scope},
        "columns": columns,
        "indexed": indexed_of(synced, columns),
    }
    return {**described, "hash": hashlib.sha256(json.dumps(described).encode()).hexdigest()}


def _parameterless(path):
    # The path's fixed parts - a parameter named either way between them.
    return re.split(r"\{[^}]*\}", path)


def _retrieved_at(generator, viewset, paths):
    # The document's path of the viewset's retrieve - its lookup named as drf-spectacular names it.
    for path, _, method, callback in generator.endpoints:
        if callback.cls is viewset and callback.actions.get(method.lower()) == "retrieve":
            return next(each for each in paths if _parameterless(each) == _parameterless(path))
    return None


def add_sync_extensions(result, generator, request, public):
    """
    A drf-spectacular postprocessing hook adding `x-sync` to each routed synced model's component schema - the one
    its `retrieve` answers with.
    """
    routes = [(path, callback) for path, _, _, callback in generator.endpoints]
    for synced in synced_models().values():
        viewset = viewset_of(synced)
        collection, detail = route(viewset, routes, "create"), _retrieved_at(generator, viewset, result["paths"])
        if collection is None or detail is None:
            continue
        schemas = result["components"]["schemas"]
        answer = result["paths"][detail]["get"]["responses"]["200"]["content"][JSON]["schema"]
        component = _resolved(answer, schemas)
        columns = {column: column_type(schema, schemas) for column, schema in component["properties"].items()}
        component["x-sync"] = x_sync(synced, collection, columns)
    return result
