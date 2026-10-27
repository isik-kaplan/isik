"""Replies, edits and deletes through UserCommentMixin - on a built model (Post) and a bound one (Thread)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from isik.django.apps.feedback.comments._comments import check_parent
from tests.testapp.models import Article, EmailUser, Post, Thread


pytestmark = pytest.mark.django_db


@pytest.fixture(params=[Post, Thread], ids=["built", "bound"])
def host(request):
    return request.param.objects.create(title="hello")


def make_user(name):
    return EmailUser.objects.create(username=name, email=f"{name}@example.com")


@pytest.fixture
def alice():
    return make_user("alice")


@pytest.fixture
def bob():
    return make_user("bob")


class TestReplies:
    def test_a_reply_is_on_its_parents_target(self, host, alice, bob):
        top = alice.comment(host, "top")

        reply = bob.reply(top, "reply")

        assert (reply.parent, reply.user, reply.body) == (top, bob, "reply")
        assert getattr(reply, f"{type(host).comments.config.target_name}_id") == host.pk

    def test_replies_go_as_deep_as_max_depth_and_no_deeper(self, host, alice):
        one = alice.reply(alice.comment(host, "0"), "1")
        two = alice.reply(one, "2")

        with pytest.raises(ValidationError) as raised:
            alice.reply(two, "3")

        assert raised.value.messages == ["Replies go 2 deep at most - this one would be deeper."]

    def test_a_comment_that_isnt_threaded_takes_no_reply(self, alice):
        article = Article.objects.create(title="hello")
        doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}]}

        with pytest.raises(ValidationError) as raised:
            alice.reply(alice.comment(article, doc), doc)

        assert raised.value.messages == ["These comments aren't threaded - a comment can't reply to another."]


class TestCheckParent:
    def test_a_parent_on_another_target_is_refused(self, host, alice):
        other = type(host).objects.create(title="other")
        config = type(host).comments.config

        with pytest.raises(ValidationError) as raised:
            check_parent(config, alice.comment(other, "there"), host.pk)

        assert raised.value.messages == ["A reply has to be on the same thing as the comment it replies to."]

    def test_without_a_max_depth_a_thread_goes_on(self, host, alice, monkeypatch):
        config = type(host).comments.config
        monkeypatch.setattr(config, "max_depth", None)
        deepest = alice.comment(host, "0")
        for depth in range(5):
            deepest = alice.reply(deepest, str(depth + 1))

        check_parent(config, deepest, host.pk)

    def test_a_depth_of_zero_takes_no_reply(self, host, alice, monkeypatch):
        config = type(host).comments.config
        monkeypatch.setattr(config, "max_depth", 0)

        with pytest.raises(ValidationError) as raised:
            check_parent(config, alice.comment(host, "top"), host.pk)

        assert raised.value.messages == ["Replies go 0 deep at most - this one would be deeper."]

    def test_the_walk_up_stops_once_its_too_deep(self, host, alice, monkeypatch, django_assert_num_queries):
        config = type(host).comments.config
        monkeypatch.setattr(config, "max_depth", 1)
        # Deeper than replies would make - built directly, as a project's data written before the limit was.
        deepest = alice.comment(host, "0")
        for depth in range(4):
            deepest = type(deepest).objects.create(
                **{config.target_name: host}, user=alice, body=str(depth + 1), parent=deepest
            )

        with django_assert_num_queries(1), pytest.raises(ValidationError):
            check_parent(config, deepest, host.pk)


class TestEditsAndDeletes:
    def test_its_author_edits_it_and_the_edit_is_stamped(self, host, alice):
        comment = alice.comment(host, "first")

        alice.edit_comment(comment, "second")

        comment.refresh_from_db()
        assert comment.body == "second"
        assert comment.edited_at is not None

    def test_an_edit_without_a_stamp_to_set(self, alice):
        article = Article.objects.create(title="hello")
        doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}]}
        comment = alice.comment(article, doc)

        alice.edit_comment(
            comment, {**doc, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "yo"}]}]}
        )

        comment.refresh_from_db()
        assert comment.body["content"][0]["content"][0]["text"] == "yo"

    def test_only_its_author_edits_or_deletes_it(self, host, alice, bob):
        comment = alice.comment(host, "mine")

        with pytest.raises(PermissionDenied, match=r"^Only its author can edit a comment\.$"):
            bob.edit_comment(comment, "yours")
        with pytest.raises(PermissionDenied, match=r"^Only its author can delete a comment\.$"):
            bob.delete_comment(comment)

    def test_deleting_one_deletes_its_replies(self, host, alice, bob):
        top = alice.comment(host, "top")
        bob.reply(top, "reply")

        alice.delete_comment(top)

        assert not host.comments.exists()

    def test_only_a_feedback_comment_is_edited(self, host, alice):
        with pytest.raises(TypeError, match=r"^Post isn't a model of"):
            alice.edit_comment(Post(title="x"), "body")
        with pytest.raises(TypeError, match=r"^Post isn't a model of"):
            alice.delete_comment(Post(title="x"))
