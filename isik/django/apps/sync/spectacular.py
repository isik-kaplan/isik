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

from isik.django.apps.sync.declarations import synced_models
from isik.django.apps.sync.reading import view_for, viewset_of


PROTOCOL = 1
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
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
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
    text = json.dumps(described, sort_keys=True, separators=(",", ":"))
    return {**described, "hash": hashlib.sha256(text.encode()).hexdigest()}


def _collection(viewset, endpoints):
    for path, _, method, callback in endpoints:
        if getattr(callback, "cls", None) is viewset and callback.actions.get(method.lower()) in ("list", "create"):
            return path
    return None


def _component(serializer_class, registry):
    # The response's component: of those drf-spectacular made from the serializer, the shortest name - a request
    # component's adds to it.
    names = [
        component.name
        for component in registry._components.values()
        if component.type == "schemas"
        and (component.object is serializer_class or type(component.object) is serializer_class)
    ]
    return min(names, key=len) if names else None


def add_sync_extensions(result, generator, request, public):
    """A drf-spectacular postprocessing hook adding `x-sync` to each routed synced model's component schema."""
    schemas = result.get("components", {}).get("schemas", {})
    for synced in synced_models().values():
        viewset = viewset_of(synced)
        path = _collection(viewset, generator.endpoints)
        name = _component(view_for(viewset, None, "retrieve").get_serializer_class(), generator.registry)
        if path is None or name not in schemas:
            continue
        properties = schemas[name].get("properties", {})
        columns = {column: column_type(schema, schemas) for column, schema in properties.items()}
        schemas[name]["x-sync"] = x_sync(synced, path, columns)
    return result
