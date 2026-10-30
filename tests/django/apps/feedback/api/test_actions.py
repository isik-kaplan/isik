"""The feedback actions on a host's viewset - each run on posts (built by the makers) and threads (bound)."""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from rest_framework import serializers
from rest_framework.test import APIClient

from isik.django.drf.serializers.base import BaseModelSerializer
from tests.django.apps.feedback.api.views import UnpickedViewSet
from tests.testapp.models import Article, EmailUser, MultiVoteHost, Post, Thread


pytestmark = pytest.mark.django_db

HOSTS = {"posts": Post, "threads": Thread}


@pytest.fixture(params=sorted(HOSTS))
def route(request):
    return request.param


@pytest.fixture
def host(route):
    return HOSTS[route].objects.create(title="hello")


def make_user(name):
    return EmailUser.objects.create(username=name, email=f"{name}@example.com")


@pytest.fixture
def alice():
    return make_user("alice")


@pytest.fixture
def bob():
    return make_user("bob")


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def keyed():
    return {"Idempotency-Key": str(uuid.uuid4())}


class TestVotes:
    def test_put_sets_the_callers_vote_and_answers_with_the_hosts_votes(self, route, host, alice, bob):
        client_for(bob).put(f"/{route}/{host.pk}/vote/", {"value": -1}, format="json")

        response = client_for(alice).put(f"/{route}/{host.pk}/vote/", {"value": 1}, format="json")

        assert response.status_code == 200
        assert response.json() == {"score": 0, "up": 1, "down": 1, "mine": 1}

    def test_a_second_put_changes_the_vote(self, route, host, alice):
        client = client_for(alice)
        client.put(f"/{route}/{host.pk}/vote/", {"value": 1}, format="json")

        response = client.put(f"/{route}/{host.pk}/vote/", {"value": -1}, format="json")

        assert response.json() == {"score": -1, "up": 0, "down": 1, "mine": -1}
        assert host.votes.count() == 1

    def test_delete_takes_it_back(self, route, host, alice):
        client = client_for(alice)
        client.put(f"/{route}/{host.pk}/vote/", {"value": 1}, format="json")

        response = client.delete(f"/{route}/{host.pk}/vote/")

        assert response.json() == {"score": 0, "up": 0, "down": 0, "mine": None}

    @pytest.mark.parametrize("value", [0, 2, "up", None])
    def test_only_one_or_minus_one_is_a_vote(self, route, host, alice, value):
        response = client_for(alice).put(f"/{route}/{host.pk}/vote/", {"value": value}, format="json")

        assert response.status_code == 400
        assert list(response.json()) == ["value"]

    def test_signing_in_comes_first(self, route, host):
        assert client_for().put(f"/{route}/{host.pk}/vote/", {"value": 1}, format="json").status_code == 403

    def test_a_host_the_viewset_cant_see_has_nothing_to_vote_on(self, route, alice):
        hidden = HOSTS[route].objects.create(title="hidden one")

        assert client_for(alice).put(f"/{route}/{hidden.pk}/vote/", {"value": 1}, format="json").status_code == 404

    def test_the_vote_field_picks_one_of_several(self, alice):
        answer = MultiVoteHost.objects.create()

        response = client_for(alice).put(f"/answers/{answer.pk}/vote/", {"value": 1}, format="json")

        assert response.json()["score"] == 1
        assert (answer.helpfulness.count(), answer.upvotes.count()) == (1, 0)

    def test_several_without_a_vote_field_is_refused(self, alice):
        answer = MultiVoteHost.objects.create()

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^UnpickedViewSet can't tell which of MultiVoteHost's upvotes, helpfulness to use - set "
            r"vote_field to one of them\.$",
        ):
            client_for(alice).put(f"/unpicked/{answer.pk}/vote/", {"value": 1}, format="json")

    def test_a_host_without_votes_is_refused(self):
        from isik.django.apps.feedback.viewsets import VoteActions
        from tests.django.apps.feedback.api.views import ArticleViewSet

        view = type("ArticleVotes", (VoteActions, ArticleViewSet), {"__module__": __name__})()

        with pytest.raises(
            ImproperlyConfigured, match=r"^ArticleVotes's routes need a votes\(\) on Article, which has none\.$"
        ):
            view.feedback_attachment(view.feedback_kind, view.feedback_option, Article)

    def test_a_vote_field_naming_none_of_them_is_refused(self, alice):
        answer = MultiVoteHost.objects.create()
        view = type("Misnamed", (UnpickedViewSet,), {"__module__": __name__, "vote_field": "nope"})()

        with pytest.raises(ImproperlyConfigured, match=r"^Misnamed can't tell which of MultiVoteHost's upvotes"):
            view.feedback_attachment(view.feedback_kind, view.feedback_option, type(answer))


class TestBookmarks:
    def test_put_bookmarks_it_and_delete_takes_it_back(self, route, host, alice, bob):
        client_for(bob).put(f"/{route}/{host.pk}/bookmark/")
        client = client_for(alice)

        assert client.put(f"/{route}/{host.pk}/bookmark/").json() == {"count": 2, "mine": True}
        assert client.put(f"/{route}/{host.pk}/bookmark/").json() == {"count": 2, "mine": True}
        assert client.delete(f"/{route}/{host.pk}/bookmark/").json() == {"count": 1, "mine": False}


class TestComments:
    def post(self, client, route, host, data):
        return client.post(f"/{route}/{host.pk}/comments/", data, format="json", headers=keyed())

    def test_a_new_comment_is_the_callers(self, route, host, alice):
        response = self.post(client_for(alice), route, host, {"body": "first"})

        assert response.status_code == 201
        assert response.json()["body"] == "first"
        assert response.json()["user"] == str(alice.pk)
        assert response.json()["parent"] is None
        assert response.json()["reply_count"] == 0

    def test_a_parent_given_as_null_is_a_top_level_comment(self, route, host, alice):
        response = self.post(client_for(alice), route, host, {"body": "first", "parent": None})

        assert response.status_code == 201
        assert response.json()["parent"] is None

    def test_a_new_comment_takes_an_idempotency_key(self, route, host, alice):
        response = client_for(alice).post(f"/{route}/{host.pk}/comments/", {"body": "first"}, format="json")

        assert response.status_code == 400

    def test_anyone_reads_them_a_page_at_a_time_oldest_first(self, route, host, alice):
        client = client_for(alice)
        for body in ("one", "two", "three"):
            self.post(client, route, host, {"body": body})

        first = client_for().get(f"/{route}/{host.pk}/comments/").json()
        second = client_for().get(f"/{route}/{host.pk}/comments/?page=2").json()

        assert first["count"] == 3
        assert [row["body"] for row in first["results"]] == ["one", "two"]
        assert [row["body"] for row in second["results"]] == ["three"]

    def test_a_reply_and_the_filters_for_a_thread(self, route, host, alice):
        client = client_for(alice)
        top = self.post(client, route, host, {"body": "top"}).json()
        reply = self.post(client, route, host, {"body": "reply", "parent": top["id"]}).json()
        self.post(client, route, host, {"body": "other"})

        everything = client.get(f"/{route}/{host.pk}/comments/").json()["results"]
        replies = client.get(f"/{route}/{host.pk}/comments/?parent={top['id']}").json()["results"]
        top_level = client.get(f"/{route}/{host.pk}/comments/?top_level=true").json()["results"]

        assert reply["parent"] == top["id"]
        assert [(row["body"], row["reply_count"]) for row in everything] == [("top", 1), ("reply", 0)]
        assert [row["body"] for row in replies] == ["reply"]
        assert [row["body"] for row in top_level] == ["top", "other"]

    def test_a_parent_that_isnt_one_is_refused(self, route, host, alice):
        response = client_for(alice).get(f"/{route}/{host.pk}/comments/?parent=nope")

        assert response.status_code == 400
        assert list(response.json()) == ["parent"]

    def test_a_reply_goes_on_its_parents_host(self, route, host, alice):
        other = HOSTS[route].objects.create(title="other")
        client = client_for(alice)
        elsewhere = self.post(client, route, other, {"body": "there"}).json()

        response = self.post(client, route, host, {"body": "here", "parent": elsewhere["id"]})

        assert response.status_code == 400
        assert response.json() == {"parent": ["A reply has to be on the same thing as the comment it replies to."]}

    def test_replies_go_as_deep_as_max_depth(self, route, host, alice):
        client = client_for(alice)
        top = self.post(client, route, host, {"body": "0"}).json()
        one = self.post(client, route, host, {"body": "1", "parent": top["id"]}).json()
        two = self.post(client, route, host, {"body": "2", "parent": one["id"]})

        response = self.post(client, route, host, {"body": "3", "parent": two.json()["id"]})

        assert two.status_code == 201
        assert response.status_code == 400
        assert response.json() == {"parent": ["Replies go 2 deep at most - this one would be deeper."]}

    def test_its_author_edits_it_and_the_edit_is_stamped(self, route, host, alice):
        client = client_for(alice)
        made = self.post(client, route, host, {"body": "first"}).json()

        response = client.patch(f"/{route}/{host.pk}/comments/{made['id']}/", {"body": "second"}, format="json")

        assert response.status_code == 200
        assert response.json()["body"] == "second"
        assert response.json()["edited_at"] is not None
        assert made["edited_at"] is None

    def test_an_edit_cant_move_it(self, route, host, alice):
        client = client_for(alice)
        top = self.post(client, route, host, {"body": "top"}).json()
        made = self.post(client, route, host, {"body": "second"}).json()

        response = client.patch(f"/{route}/{host.pk}/comments/{made['id']}/", {"parent": top["id"]}, format="json")

        assert response.status_code == 400
        assert response.json() == {"parent": ["A comment can't be moved under another one."]}

    def test_only_its_author_changes_it(self, route, host, alice, bob):
        made = self.post(client_for(alice), route, host, {"body": "mine"}).json()
        url = f"/{route}/{host.pk}/comments/{made['id']}/"

        assert client_for(bob).patch(url, {"body": "yours"}, format="json").status_code == 403
        assert client_for(bob).delete(url).status_code == 403
        assert client_for().delete(url).status_code == 403
        assert client_for(alice).delete(url).status_code == 204
        assert client_for(alice).delete(url).status_code == 404

    def test_a_comment_on_another_host_isnt_found_here(self, route, host, alice):
        other = HOSTS[route].objects.create(title="other")
        made = self.post(client_for(alice), route, other, {"body": "there"}).json()

        assert client_for(alice).delete(f"/{route}/{host.pk}/comments/{made['id']}/").status_code == 404

    def test_a_moderator_changes_the_comments_it_moderates(self, alice):
        post = Post.objects.create(title="hello")
        made = self.post(client_for(alice), "posts", post, {"body": "mine"}).json()
        kept = self.post(client_for(alice), "posts", post, {"body": "kept"}).json()
        moderator = client_for(make_user("moderator"))

        assert moderator.delete(f"/moderated-posts/{post.pk}/comments/{made['id']}/").status_code == 204
        assert moderator.delete(f"/moderated-posts/{post.pk}/comments/{kept['id']}/").status_code == 403

    def test_comments_that_arent_threaded_take_no_thread_filter(self, alice):
        article = Article.objects.create(title="hello")
        client = client_for(alice)

        made = client.post(
            f"/articles/{article.pk}/comments/",
            {"body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}]}},
            format="json",
            headers=keyed(),
        )
        response = client.get(f"/articles/{article.pk}/comments/?top_level=true")

        assert made.status_code == 201
        assert "parent" not in made.json()
        assert response.status_code == 400
        assert response.json() == {"parent": ["These comments aren't threaded."]}


class TestNotes:
    def post(self, client, route, host, body):
        return client.post(f"/{route}/{host.pk}/notes/", {"body": body}, format="json", headers=keyed())

    def test_each_caller_has_only_their_own(self, route, host, alice, bob):
        self.post(client_for(alice), route, host, "alice's")
        self.post(client_for(bob), route, host, "bob's")

        response = client_for(alice).get(f"/{route}/{host.pk}/notes/")

        assert [row["body"] for row in response.json()["results"]] == ["alice's"]

    def test_nobody_signed_out_has_any(self, route, host):
        assert client_for().get(f"/{route}/{host.pk}/notes/").status_code == 403

    def test_another_callers_note_isnt_found(self, route, host, alice, bob):
        made = self.post(client_for(alice), route, host, "alice's").json()
        url = f"/{route}/{host.pk}/notes/{made['id']}/"

        assert client_for(bob).patch(url, {"body": "bob's"}, format="json").status_code == 404
        assert client_for(alice).patch(url, {"body": "edited"}, format="json").json()["body"] == "edited"


class TestTheHostsOwnRoutes:
    def test_a_list_reads_each_rows_feedback_in_one_query(self, route, alice, bob, django_assert_num_queries):
        model = HOSTS[route]
        first, second = model.objects.create(title="a"), model.objects.create(title="b")
        alice.upvote(first)
        bob.upvote(first)
        alice.downvote(second)
        alice.bookmark(second)
        alice.comment(first, "hi")
        alice.add_note(second, "later")
        client = client_for(alice)

        with django_assert_num_queries(4):  # the request's savepoint and its release, the count, the page
            response = client.get(f"/{route}/?ordering=title")

        rows = {row["title"]: row for row in response.json()["results"]}
        assert rows["a"]["votes"] == {"score": 2, "up": 2, "down": 0, "mine": 1}
        assert rows["b"]["votes"] == {"score": -1, "up": 0, "down": 1, "mine": -1}
        assert (rows["a"]["bookmarks"], rows["b"]["bookmarks"]) == (
            {"count": 0, "mine": False},
            {"count": 1, "mine": True},
        )
        assert (rows["a"]["comment_count"], rows["b"]["note_count"]) == (1, 1)


DOC = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}]}


class TestRowDetails:
    def post(self, client, route, host, data):
        return client.post(f"/{route}/{host.pk}/comments/", data, format="json", headers=keyed())

    def test_a_list_takes_only_and_exclude(self, route, host, alice):
        self.post(client_for(alice), route, host, {"body": "first"})

        rows = client_for(alice).get(f"/{route}/{host.pk}/comments/?only=body").json()["results"]

        assert rows == [{"body": "first"}]

    def test_without_pagination_its_every_row(self, alice):
        article = Article.objects.create(title="hello")
        client = client_for(alice)
        client.post(f"/articles/{article.pk}/comments/", {"body": DOC}, format="json", headers=keyed())

        response = client.get(f"/articles/{article.pk}/comments/?only=body")

        assert response.json() == [{"body": DOC}]

    def test_the_oldest_first_whatever_their_keys(self, alice):
        thread = Thread.objects.create(title="hello")
        model = Thread.comments.model
        ids = sorted(uuid.uuid4() for _each in range(3))
        start = timezone.now()
        # The oldest keyed last, so a key's order and a time's can't agree.
        for minutes, pk in enumerate(reversed(ids)):
            model.objects.create(
                id=pk, thread=thread, user=alice, body=str(pk), created_at=start + timedelta(minutes=minutes)
            )

        rows = client_for(alice).get(f"/threads/{thread.pk}/comments/?only=id").json()["results"]

        assert [row["id"] for row in rows] == [str(pk) for pk in reversed(ids)][:2]

    def test_ties_go_by_primary_key(self, alice):
        thread = Thread.objects.create(title="hello")
        model = Thread.comments.model
        ids = sorted(uuid.uuid4() for _each in range(3))
        at = timezone.now()
        # Made in reverse at one moment - only the key orders them.
        for pk in reversed(ids):
            model.objects.create(id=pk, thread=thread, user=alice, body=str(pk), created_at=at)

        rows = client_for(alice).get(f"/threads/{thread.pk}/comments/?only=id").json()["results"]

        assert [row["id"] for row in rows] == [str(pk) for pk in ids[:2]]

    def test_top_level_takes_one_too(self, alice):
        post = Post.objects.create(title="hello")
        top = alice.comment(post, "top")
        alice.reply(top, "reply")

        rows = client_for(alice).get(f"/posts/{post.pk}/comments/?top_level=1&only=body").json()["results"]

        assert rows == [{"body": "top"}]

    def test_an_edit_saves_no_stamp_where_the_model_has_none(self, alice, monkeypatch):
        from tests.django.apps.feedback.api.views import ArticleViewSet

        saved = []

        class Recording(serializers.ModelSerializer):
            class Meta:
                model = Article.comments.model
                fields = ["body"]

            def save(self, **kwargs):
                saved.append(kwargs)
                return super().save(**kwargs)

        monkeypatch.setattr(ArticleViewSet, "comment_serializer_class", Recording)
        article = Article.objects.create(title="hello")
        made = alice.comment(article, DOC)

        client_for(alice).patch(f"/articles/{article.pk}/comments/{made.pk}/", {"body": DOC}, format="json")

        assert saved == [{}]

    def test_an_edit_where_nothing_is_stamped(self, alice):
        article = Article.objects.create(title="hello")
        made = alice.comment(article, DOC)
        edited = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "yo"}]}]}

        response = client_for(alice).patch(
            f"/articles/{article.pk}/comments/{made.pk}/", {"body": edited}, format="json"
        )

        assert response.status_code == 200
        assert response.json()["body"] == edited

    def test_an_edit_is_validated(self, route, host, alice):
        made = self.post(client_for(alice), route, host, {"body": "first"}).json()

        response = client_for(alice).patch(f"/{route}/{host.pk}/comments/{made['id']}/", {"body": ""}, format="json")

        assert response.status_code == 400
        assert list(response.json()) == ["body"]

    def test_another_callers_comment_is_refused_saying_why(self, route, host, alice, bob):
        made = self.post(client_for(alice), route, host, {"body": "mine"}).json()

        response = client_for(bob).delete(f"/{route}/{host.pk}/comments/{made['id']}/")

        assert response.json() == {"detail": "Only its author can change it."}


class TestSerializers:
    def test_a_views_own_serializer_wins(self, alice, monkeypatch):
        from tests.django.apps.feedback.api.views import PostViewSet

        class Own(serializers.ModelSerializer):
            class Meta:
                model = Post.comments.model
                fields = ["body"]

        monkeypatch.setattr(PostViewSet, "comment_serializer_class", Own)
        post = Post.objects.create(title="hello")
        alice.comment(post, "hi")

        assert client_for(alice).get(f"/posts/{post.pk}/comments/").json()["results"] == [{"body": "hi"}]

    def test_the_serializer_registered_for_the_model(self, alice, monkeypatch):
        monkeypatch.setattr(BaseModelSerializer, "model_map", {})

        class Registered(BaseModelSerializer):
            class Meta:
                model = Thread.notes.model
                fields = ["id", "body"]

        thread = Thread.objects.create(title="hello")
        alice.add_note(thread, "mine")

        assert list(client_for(alice).get(f"/threads/{thread.pk}/notes/").json()["results"][0]) == ["id", "body"]

    def test_else_the_generic_one_made_and_registered(self, alice, monkeypatch):
        monkeypatch.setattr(BaseModelSerializer, "model_map", {})
        thread = Thread.objects.create(title="hello")
        alice.add_note(thread, "mine")

        row = client_for(alice).get(f"/threads/{thread.pk}/notes/").json()["results"][0]

        assert set(row) == {"id", "body", "created_at", "updated_at", "user"}
        assert BaseModelSerializer.get_for_model(Thread.notes.model).__name__ == "ThreadNoteSerializer"


class TestTheHostsAnnotations:
    def view(self, view_class, action, user):
        view = view_class()
        view.action = action
        view.request = type("Request", (), {"user": user})()
        return view

    @pytest.mark.parametrize("action", ["list", "retrieve"])
    def test_list_and_retrieve_carry_the_views_own_attachments(self, action, alice):
        from tests.django.apps.feedback.api.views import HelpfulnessViewSet

        queryset = self.view(HelpfulnessViewSet, action, alice).get_queryset()

        assert set(queryset.query.annotations) == {
            "helpfulness_score",
            "helpfulness_up",
            "helpfulness_down",
            "helpfulness_mine",
        }

    def test_other_actions_carry_none(self, alice):
        from tests.django.apps.feedback.api.views import HelpfulnessViewSet

        assert self.view(HelpfulnessViewSet, "vote", alice).get_queryset().query.annotations == {}

    def test_one_row_reads_its_feedback_in_its_own_query(self, route, host, alice, django_assert_num_queries):
        alice.upvote(host)

        with django_assert_num_queries(3):  # the request's savepoint and its release, the row
            response = client_for(alice).get(f"/{route}/{host.pk}/")

        assert response.json()["votes"] == {"score": 1, "up": 1, "down": 0, "mine": 1}
