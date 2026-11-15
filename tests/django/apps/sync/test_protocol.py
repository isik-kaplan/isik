"""
isik's server against the sync protocol's fixtures - `tests/protocol/sync/v1`, pinned from isik-ts: every step's
response matched, every message validated against the protocol's schema.
"""

import json
from pathlib import Path

import jsonschema
import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from isik.django.conf import override_isik
from tests.django.apps.sync import support


PROTOCOL = Path(__file__).resolve().parents[3] / "protocol" / "sync" / "v1"
SCHEMA = json.loads((PROTOCOL / "schema.json").read_text())
FIXTURES = sorted((PROTOCOL / "fixtures").glob("*.json"))
# What each endpoint's answer validates as, by status.
ANSWERS = {
    ("/sync/changes", 200): "ChangesResponse",
    ("/sync/changes", 410): "CursorExpired",
    ("/sync/push", 200): "PushResponse",
    ("/sync/push", 413): "PushTooLarge",
    ("/sync/capabilities", 200): "Capabilities",
}


def valid(value, definition):
    jsonschema.validate(value, {**SCHEMA, "$ref": f"#/$defs/{definition}"})


def bound(value, names):
    """`value` with each placeholder replaced by what it's bound to."""
    if isinstance(value, str) and value in names:
        return names[value]
    if isinstance(value, dict):
        return {key: bound(each, names) for key, each in value.items()}
    if isinstance(value, list):
        return [bound(each, names) for each in value]
    return value


def matched(expected, actual, names, where="body"):
    """Matches `actual` against `expected` as the protocol's fixtures say, binding placeholders in `names`."""
    if isinstance(expected, str) and expected.startswith("$"):
        if expected == "$any":
            return
        if expected in names:
            assert actual == names[expected], f"{where}: {expected} is {names[expected]!r}, not {actual!r}"
            return
        if expected.startswith("$etag:"):
            others = {value for name, value in names.items() if name.startswith("$etag:")}
            assert actual not in others, f"{where}: {expected} binds {actual!r}, another etag's value"
        names[expected] = actual
        return
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) == set(expected), f"{where}: {actual!r} isn't {expected!r}"
        for key, value in expected.items():
            matched(value, actual[key], names, f"{where}.{key}")
        return
    if isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{where}: {actual!r} isn't {expected!r}"
        for index, value in enumerate(expected):
            matched(value, actual[index], names, f"{where}[{index}]")
        return
    assert actual == expected, f"{where}: {actual!r} isn't {expected!r}"


def answered(client, request, names):
    query = bound(request.get("query", {}), names)
    headers = bound(request.get("headers", {}), names)
    if request["method"] == "GET":
        return client.get(request["path"], query, headers=headers)
    body = bound(request.get("body"), names)
    valid(body, "PushRequest")
    return client.post(request["path"], body, format="json", headers=headers)


@pytest.mark.django_db(transaction=True)
@override_settings(ROOT_URLCONF="tests.testapp.sync_urls")
@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_the_fixture_holds(path):
    fixture = json.loads(path.read_text())
    valid(fixture, "Fixture")
    member = support.given(fixture["given"])
    client = APIClient()
    if member is not None:
        client.force_authenticate(member)
    names = {}
    with override_isik(
        DJANGO={
            "SYNC": {
                "PUSH": {
                    "OPTIONS": dict.fromkeys(("MAX_ENTRIES", "MAX_BYTES"))
                    | {key.upper(): value for key, value in fixture["given"].get("push", {}).items()}
                }
            }
        }
    ):
        for number, step in enumerate(fixture["steps"]):
            if "act" in step:
                support.act(member, step["act"])
                continue
            response = answered(client, step["request"], names)
            expected = step["response"]
            where = f"step {number}"
            assert response.status_code == expected["status"], f"{where}: {response.status_code} {response.content!r}"
            for header, value in expected.get("headers", {}).items():
                matched(value, response.headers.get(header), names, f"{where} {header}")
            if "body" in expected:
                matched(expected["body"], response.json(), names, f"{where} body")
            answer = ANSWERS.get((step["request"]["path"], response.status_code))
            if answer is not None:
                valid(response.json(), answer)
