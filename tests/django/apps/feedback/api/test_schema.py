"""What the feedback actions publish - each route's operation, a page for a list, the summaries."""

import pytest
from django.test import override_settings
from drf_spectacular.generators import SchemaGenerator

from tests.django.apps.feedback.api import urls


pytestmark = pytest.mark.django_db


@pytest.fixture
def schema():
    # Per test, not per module: mutation testing runs a mutant only against the tests that ran its code.
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
        return SchemaGenerator(patterns=urls.described).get_schema(request=None, public=True)


def body(operation, direction="response", code="200"):
    holder = operation["requestBody"] if direction == "request" else operation["responses"][code]
    return holder["content"]["application/json"]["schema"]


def parameters(operation):
    return {parameter["name"]: parameter for parameter in operation.get("parameters", [])}


def test_a_vote_takes_its_value_and_both_methods_answer_with_the_votes(schema):
    put, delete = schema["paths"]["/posts/{id}/vote/"]["put"], schema["paths"]["/posts/{id}/vote/"]["delete"]

    assert body(put, "request") == {"$ref": "#/components/schemas/VoteInput"}
    assert body(put) == body(delete) == {"$ref": "#/components/schemas/VoteSummary"}
    assert "requestBody" not in delete
    assert "Idempotency-Key" not in parameters(put)
    assert "If-Match" not in parameters(put)
    assert "only" not in parameters(put)
    assert set(schema["components"]["schemas"]["VoteSummary"]["properties"]) == {"score", "up", "down", "mine"}


def test_a_bookmark_takes_nothing_and_answers_with_the_bookmarks(schema):
    for method in ("put", "delete"):
        operation = schema["paths"]["/posts/{id}/bookmark/"][method]

        assert "requestBody" not in operation
        assert body(operation) == {"$ref": "#/components/schemas/BookmarkSummary"}
        assert "only" not in parameters(operation)


def test_comments_are_a_page_with_their_thread_filters(schema):
    listed = schema["paths"]["/posts/{id}/comments/"]["get"]

    assert listed["operationId"] == "posts_comments_list"
    assert body(listed) == {"$ref": "#/components/schemas/PaginatedPostCommentsCommentList"}
    assert {"page", "parent", "top_level"} <= set(parameters(listed))
    assert "title" not in parameters(listed)


def test_a_new_comment_is_created_with_a_key(schema):
    created = schema["paths"]["/posts/{id}/comments/"]["post"]

    assert body(created, code="201") == {"$ref": "#/components/schemas/PostCommentsComment"}
    assert parameters(created)["Idempotency-Key"]["required"] is True


def test_one_comment_is_named_in_its_path(schema):
    one = schema["paths"]["/posts/{id}/comments/{comment_pk}/"]

    assert set(one) == {"patch", "delete"}
    assert parameters(one["patch"])["comment_pk"]["in"] == "path"
    assert one["delete"]["responses"]["204"]["description"] == "No response body"


def test_notes_are_a_page_and_one_is_named_in_its_path(schema):
    assert body(schema["paths"]["/threads/{id}/notes/"]["get"]) == {
        "$ref": "#/components/schemas/PaginatedThreadNoteList"
    }
    assert body(schema["paths"]["/threads/{id}/notes/"]["post"], code="201") == {
        "$ref": "#/components/schemas/ThreadNote"
    }
    assert parameters(schema["paths"]["/threads/{id}/notes/{note_pk}/"]["patch"])["note_pk"]["in"] == "path"


def test_the_hosts_feedback_fields_are_described(schema):
    post = schema["components"]["schemas"]["Post"]["properties"]

    assert post["votes"]["allOf"] == [{"$ref": "#/components/schemas/VoteSummary"}]
    assert post["bookmarks"]["allOf"] == [{"$ref": "#/components/schemas/BookmarkSummary"}]
    assert post["comment_count"]["type"] == post["note_count"]["type"] == "integer"
