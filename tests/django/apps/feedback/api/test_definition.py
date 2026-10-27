"""What the feedback mixins set up on a viewset as its class is made - redone here, at test time."""

import pytest
from drf_spectacular.utils import OpenApiParameter

from isik.django.apps.feedback.viewsets import (
    BookmarkActions,
    CommentActions,
    NoteActions,
    VoteActions,
    _own_exemptions,
    _path_parameter,
)
from isik.django.apps.idempotency.drf import NoIdempotencyKey, NoReplay
from isik.django.drf.viewsets import BaseModelViewSet
from isik.django.drf.viewsets.preconditions import NoPrecondition
from isik.django.drf.viewsets.registry import ViewSetRegistryExemption
from tests.django.apps.feedback.api.views import PostSerializer
from tests.testapp.models import Post


TEST_VIEWSET = ViewSetRegistryExemption(reason="a test's own viewset, made again on every run of it")
OWN_KEY = NoIdempotencyKey(reason="a test's own reason, for an action it exempts itself")


def viewset(*mixins, **attrs):
    return type(
        "Made",
        (*mixins, BaseModelViewSet),
        {
            "__module__": __name__,
            "exempt_from_registry": TEST_VIEWSET,
            "model": Post,
            "endpoint": "made",
            "serializer_class": PostSerializer,
            **attrs,
        },
    )


class TestOwnExemptions:
    def test_each_is_isiks_own_and_says_why(self):
        made = _own_exemptions()

        assert set(made) == {"precondition", "key", "replay"}
        assert isinstance(made["precondition"], NoPrecondition)
        assert made["precondition"] == "writes the caller's own feedback row, never the host row an If-Match would name"
        assert isinstance(made["key"], NoIdempotencyKey)
        assert made["key"] == "sets the caller's own row to the state the request names, so a retry changes nothing"
        assert isinstance(made["replay"], NoReplay)
        assert made["replay"] == (
            "answers with a feedback row, which a replay by reference to the host can't serialize again"
        )
        assert all(each.library is True for each in made.values())
        assert made["key"].file.endswith("viewsets.py")


class TestAViewsetsExemptions:
    def test_every_action_skips_if_match(self):
        made = viewset(VoteActions, BookmarkActions, CommentActions, NoteActions)
        reason = _own_exemptions()["precondition"]

        assert made.precondition_exempt_actions == dict.fromkeys(
            ["vote", "bookmark", "comments", "comment", "notes", "note"], reason
        )

    def test_setting_ones_own_row_skips_the_key_and_a_new_one_isnt_replayed(self):
        made = viewset(VoteActions, BookmarkActions, CommentActions, NoteActions)
        own = _own_exemptions()

        assert made.idempotency_exempt_actions == dict.fromkeys(["vote", "bookmark", "comment", "note"], own["key"])
        assert made.idempotency_no_replay_actions == dict.fromkeys(["comments", "notes"], own["replay"])

    def test_only_the_mixins_a_view_has(self):
        made = viewset(VoteActions)

        assert set(made.precondition_exempt_actions) == {"vote"}
        assert set(made.idempotency_exempt_actions) == {"vote"}
        assert made.idempotency_no_replay_actions == {}
        assert set(made.schema_actions) == {"vote"}
        assert made.schema_list_actions == ()

    def test_a_views_own_entry_wins(self):
        made = viewset(VoteActions, CommentActions, idempotency_exempt_actions={"vote": OWN_KEY, "other": OWN_KEY})

        assert made.idempotency_exempt_actions == {
            "vote": OWN_KEY,
            "comment": _own_exemptions()["key"],
            "other": OWN_KEY,
        }

    def test_a_views_own_entries_win_for_every_rule(self):
        own_precondition = NoPrecondition(reason="a test's own reason, for an action it exempts itself")
        own_replay = NoReplay(reason="a test's own reason, for an action it exempts itself")

        made = viewset(
            NoteActions,
            precondition_exempt_actions={"note": own_precondition},
            idempotency_no_replay_actions={"notes": own_replay, "other": own_replay},
        )

        assert made.precondition_exempt_actions == {
            "notes": _own_exemptions()["precondition"],
            "note": own_precondition,
        }
        assert made.idempotency_no_replay_actions == {"notes": own_replay, "other": own_replay}

    def test_a_mixin_on_a_view_without_the_write_guards(self):
        from rest_framework.viewsets import GenericViewSet

        made = type("Plain", (VoteActions, GenericViewSet), {"__module__": __name__})

        assert made.precondition_exempt_actions == {"vote": _own_exemptions()["precondition"]}
        assert made.idempotency_no_replay_actions == {}

    def test_a_subclass_keeps_them(self):
        made = viewset(NoteActions)
        again = type("Again", (made,), {"__module__": __name__, "exempt_from_registry": TEST_VIEWSET})

        assert (
            again.idempotency_no_replay_actions
            == made.idempotency_no_replay_actions
            == {"notes": _own_exemptions()["replay"]}
        )


class TestTheSchemaTheyDeclare:
    def test_each_mixins_actions_are_described(self):
        made = viewset(VoteActions, BookmarkActions, CommentActions, NoteActions)

        assert made.schema_actions == {
            **VoteActions.feedback_schema,
            **BookmarkActions.feedback_schema,
            **CommentActions.feedback_schema,
            **NoteActions.feedback_schema,
        }
        assert made.schema_list_actions == ("comments", "notes")

    def test_a_views_own_description_and_lists_come_too(self):
        own = {"vote": {"request": None}, "mine": {"status": 202}}

        made = viewset(VoteActions, NoteActions, schema_actions=own, schema_list_actions=("mine", "notes"))

        assert made.schema_actions == {**NoteActions.feedback_schema, **own}
        assert made.schema_list_actions == ("notes", "mine")

    def test_a_path_parameter(self):
        made = _path_parameter("comment_pk", "The comment's id.")

        assert (made.name, made.type, made.location, made.description) == (
            "comment_pk",
            str,
            OpenApiParameter.PATH,
            "The comment's id.",
        )


@pytest.mark.parametrize(
    ("mixin", "kind", "option"),
    [
        (VoteActions, "votes()", "vote_field"),
        (BookmarkActions, "bookmarks()", "bookmark_field"),
        (CommentActions, "comments()", "comment_field"),
        (NoteActions, "notes()", "note_field"),
    ],
)
def test_each_mixin_names_its_kind_and_its_option(mixin, kind, option):
    assert (mixin.feedback_kind.maker, mixin.feedback_option) == (kind, option)
    assert getattr(mixin, option) is None
