"""with_feedback(), ranked() and the serializer fields that read them."""

import math
import uuid

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.apps.feedback.annotations import ranked, with_feedback
from isik.django.apps.feedback.drf import BookmarksField, CommentCountField, NoteCountField, VotesField
from tests.testapp.models import EmailUser, MultiVoteHost, Post, Thread


pytestmark = pytest.mark.django_db

NAMES = [
    "votes_score",
    "votes_up",
    "votes_down",
    "votes_mine",
    "comments_count",
    "bookmarks_count",
    "bookmarks_mine",
    "notes_mine_count",
]


@pytest.fixture(params=[Post, Thread], ids=["built", "bound"])
def model(request):
    return request.param


def make_user(name):
    return EmailUser.objects.create(username=name, email=f"{name}@example.com")


@pytest.fixture
def people():
    return [make_user(name) for name in ("alice", "bob", "carol", "dave")]


def wilson(up, down, z=1.96):
    votes = up + down
    share = up / votes
    spread = z * math.sqrt((share * (1 - share) + z * z / (4 * votes)) / votes)
    return (share + z * z / (2 * votes) - spread) / (1 + z * z / votes)


class TestWithFeedback:
    def test_each_attachment_says_what_it_holds(self, model, people):
        alice, bob, carol, _dave = people
        first, second = model.objects.create(title="a"), model.objects.create(title="b")
        alice.upvote(first)
        bob.upvote(first)
        carol.downvote(first)
        bob.downvote(second)
        alice.bookmark(first)
        bob.bookmark(first)
        alice.comment(first, "one")
        bob.comment(first, "two")
        alice.add_note(first, "mine")
        bob.add_note(first, "bob's")

        rows = list(with_feedback(model.objects.order_by("title"), alice).values(*NAMES))

        assert rows == [
            dict(zip(NAMES, [1, 2, 1, 1, 2, 2, True, 1], strict=True)),
            dict(zip(NAMES, [-1, 0, 1, None, 0, 0, False, 0], strict=True)),
        ]

    @pytest.mark.parametrize("user", [None, AnonymousUser()], ids=["nobody", "anonymous"])
    def test_nobody_signed_in_has_nothing_of_their_own(self, model, people, user):
        host = model.objects.create(title="a")
        people[0].upvote(host)
        people[0].bookmark(host)
        people[0].add_note(host, "mine")

        row = with_feedback(model.objects.all(), user).values(*NAMES).get()

        assert (row["votes_mine"], row["bookmarks_mine"], row["notes_mine_count"]) == (None, False, 0)
        assert (row["votes_up"], row["bookmarks_count"]) == (1, 1)

    def test_only_the_attachments_named(self, model):
        model.objects.create(title="a")

        row = with_feedback(model.objects.all(), only=["bookmarks"]).values().get()

        assert {"bookmarks_count", "bookmarks_mine"} <= set(row)
        assert {"votes_score", "comments_count", "notes_mine_count"}.isdisjoint(row)

    def test_a_name_that_isnt_one_is_refused(self):
        with pytest.raises(
            ValueError,
            match=r"^Post has no votes\(\), comments\(\), notes\(\) or bookmarks\(\) named nope, nothing$",
        ):
            with_feedback(Post.objects.all(), only=["votes", "nothing", "nope"])

    def test_several_of_one_kind_are_each_their_own(self, people):
        alice, bob = people[:2]
        host = MultiVoteHost.objects.create()
        alice.upvote(host, field=MultiVoteHost.upvotes)
        bob.downvote(host, field=MultiVoteHost.helpfulness)

        row = with_feedback(MultiVoteHost.objects.all(), alice).values().get()

        assert (row["upvotes_score"], row["upvotes_mine"]) == (1, 1)
        assert (row["helpfulness_score"], row["helpfulness_mine"]) == (-1, None)

    def test_a_comments_votes_annotate_comments(self, people):
        alice = people[0]
        comment = alice.comment(Post.objects.create(title="a"), "hi")
        alice.downvote(comment)

        assert with_feedback(Post.comments.model.objects.all(), alice).values("votes_score").get() == {
            "votes_score": -1
        }


class TestRanked:
    SHAPES = {"a": (4, 0), "b": (3, 1), "c": (1, 0), "d": (2, 2), "e": (0, 0), "f": (0, 2), "g": (1, 3)}

    @pytest.fixture
    def hosts(self, model, people):
        """`{title: host}`, voted (up, down) as SHAPES says."""
        made = {title: model.objects.create(title=title) for title in self.SHAPES}
        for title, (up, down) in self.SHAPES.items():
            for person in people[:up]:
                person.upvote(made[title])
            for person in people[up : up + down]:
                person.downvote(made[title])
        return made

    def ranks(self, model, by):
        return dict(ranked(model.objects.all(), model.votes, by=by).values_list("title", "votes_rank"))

    def test_by_score(self, model, hosts):
        ranks = self.ranks(model, "score")

        assert ranks == {"a": 4.0, "b": 2.0, "c": 1.0, "d": 0.0, "e": 0.0, "f": -2.0, "g": -2.0}
        assert all(isinstance(rank, float) for rank in ranks.values())

    def test_by_wilson(self, model, hosts):
        expected = {title: wilson(*shape) for title, shape in self.SHAPES.items() if sum(shape)}

        assert self.ranks(model, "wilson") == pytest.approx({**expected, "e": 0.0})
        assert list(self.ranks(model, "wilson"))[:4] == ["a", "b", "c", "d"]

    def test_by_controversy(self, model, hosts):
        assert self.ranks(model, "controversial") == pytest.approx(
            {"a": 0.0, "b": 4 ** (1 / 3), "c": 0.0, "d": 4.0, "e": 0.0, "f": 0.0, "g": 4 ** (1 / 3)}
        )

    def test_its_ordered_best_first(self, model, hosts):
        titles = list(ranked(model.objects.all(), model.votes, by="controversial").values_list("title", flat=True))

        assert titles[0] == "d"
        assert set(titles[1:3]) == {"b", "g"}

    def test_it_annotates_only_its_own_attachment(self, model, monkeypatch):
        from isik.django.apps.feedback import annotations

        seen = []
        real = annotations.with_feedback
        monkeypatch.setattr(
            annotations, "with_feedback", lambda *args, **kwargs: seen.append(kwargs) or real(*args, **kwargs)
        )

        ranked(model.objects.all(), model.votes)

        assert seen == [{"only": ["votes"]}]

    def test_ties_go_to_the_higher_primary_key(self, model):
        # Made lower key first, so the order rows are stored in is the opposite of the one asked for.
        model.objects.create(pk=uuid.UUID(int=1), title="lower")
        model.objects.create(pk=uuid.UUID(int=2), title="higher")

        titles = list(ranked(model.objects.all(), model.votes).values_list("title", flat=True))

        assert titles == ["higher", "lower"]

    def test_the_caller_has_their_own_vote(self, model, hosts, people):
        assert ranked(model.objects.filter(title="a"), model.votes, user=people[0]).get().votes_mine == 1

    def test_only_votes_rank(self):
        with pytest.raises(TypeError, match=r"^ranked\(\) ranks by a votes\(\) attachment, not "):
            ranked(Post.objects.all(), Post.bookmarks)

    def test_only_its_own_rankings(self):
        with pytest.raises(ValueError, match=r"^ranked\(\) ranks by score, wilson, controversial, not 'hot'$"):
            ranked(Post.objects.all(), Post.votes, by="hot")


class TestFields:
    @pytest.fixture
    def request_of(self):
        def made(user):
            request = Request(APIRequestFactory().get("/"))
            request.user = user
            return request

        return made

    def test_an_annotated_row_is_read_without_a_query(self, model, people, request_of, django_assert_num_queries):
        alice = people[0]
        host = model.objects.create(title="a")
        alice.upvote(host)
        alice.bookmark(host)
        row = with_feedback(model.objects.all(), alice).get()
        context = {"request": request_of(alice)}
        fields = [VotesField(model.votes), BookmarksField(model.bookmarks)]
        for field in fields:
            field._context = context

        with django_assert_num_queries(0):
            read = [field.to_representation(row) for field in fields]

        assert read == [{"score": 1, "up": 1, "down": 0, "mine": 1}, {"count": 1, "mine": True}]

    def test_a_row_not_annotated_is_read_on_its_own(self, model, people, request_of):
        alice = people[0]
        host = model.objects.create(title="a")
        alice.comment(host, "hi")
        alice.add_note(host, "mine")
        people[1].add_note(host, "bob's")
        counts = CommentCountField(model.comments), NoteCountField(model.notes)
        for field in counts:
            field._context = {"request": request_of(alice)}

        assert [field.to_representation(host) for field in counts] == [1, 1]

    def test_a_row_not_annotated_is_queried_for_its_own_attachment(self, model, monkeypatch):
        from isik.django.apps.feedback import annotations

        seen = []
        real = annotations.with_feedback
        monkeypatch.setattr(
            annotations, "with_feedback", lambda *args, **kwargs: seen.append(kwargs) or real(*args, **kwargs)
        )
        field = BookmarksField(model.bookmarks)
        field._context = {}

        field.to_representation(model.objects.create(title="a"))

        assert seen == [{"only": ["bookmarks"]}]

    def test_without_a_request_its_nobodys(self, model, people):
        host = model.objects.create(title="a")
        people[0].upvote(host)
        field = VotesField(model.votes)
        field._context = {}

        assert field.to_representation(host) == {"score": 1, "up": 1, "down": 0, "mine": None}

    def test_each_takes_its_own_kind(self):
        with pytest.raises(TypeError, match=r"^VotesField takes a votes\(\) attachment - Host\.<name> - not "):
            VotesField(Post.bookmarks)
        with pytest.raises(TypeError, match=r"^NoteCountField takes a notes\(\) attachment"):
            NoteCountField("notes")

    def test_always_read_only_and_the_whole_row(self):
        field = BookmarksField(Post.bookmarks, source="bookmarks", read_only=False)

        assert (field.source, field.read_only) == ("*", True)

    def test_a_serializers_copy_keeps_the_hosts_attachment(self):
        from tests.django.apps.feedback.api.views import PostSerializer

        fields = PostSerializer().fields

        assert fields["votes"].field is Post.votes
        assert fields["note_count"].attachment is Post.notes.config
