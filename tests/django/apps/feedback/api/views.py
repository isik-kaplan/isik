"""
Host viewsets with every feedback action, served through `urls.py` - through Django's own handler, so
ATOMIC_REQUESTS wraps them as it would in a project. `posts` is built by the makers, `threads` is the
same over models written by hand; both refuse every write to the host itself, so a write that gets
through is the feedback actions' own permissions at work.
"""

from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import SAFE_METHODS, BasePermission

from isik.django.apps.feedback.drf import BookmarksField, CommentCountField, NoteCountField, VotesField
from isik.django.apps.feedback.viewsets import BookmarkActions, CommentActions, NoteActions, VoteActions
from isik.django.drf.serializers.base import BaseModelSerializer
from isik.django.drf.serializers.registry import SerializerRegistryExemption
from isik.django.drf.viewsets import BaseModelViewSet
from isik.django.drf.viewsets.registry import ViewSetRegistryExemption
from tests.testapp.models import Article, MultiVoteHost, Post, Thread


TEST_VIEWSET = ViewSetRegistryExemption(reason="a test's own viewset, one of several over the same model")
TEST_SERIALIZER = SerializerRegistryExemption(reason="a test's own serializer, one of several over the same model")


class ReadOnly(BasePermission):
    """The host's own permission: nobody writes a post or a thread here."""

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class Pages(PageNumberPagination):
    page_size = 2


class PostSerializer(BaseModelSerializer):
    exempt_from_registry = TEST_SERIALIZER
    votes = VotesField(Post.votes)
    bookmarks = BookmarksField(Post.bookmarks)
    comment_count = CommentCountField(Post.comments)
    note_count = NoteCountField(Post.notes)

    class Meta:
        model = Post
        fields = ["id", "title", "votes", "bookmarks", "comment_count", "note_count"]


class ThreadSerializer(BaseModelSerializer):
    exempt_from_registry = TEST_SERIALIZER
    votes = VotesField(Thread.votes)
    bookmarks = BookmarksField(Thread.bookmarks)
    comment_count = CommentCountField(Thread.comments)
    note_count = NoteCountField(Thread.notes)

    class Meta:
        model = Thread
        fields = ["id", "title", "votes", "bookmarks", "comment_count", "note_count"]


class _Host(VoteActions, BookmarkActions, CommentActions, NoteActions, BaseModelViewSet):
    is_base_class = True
    permission_classes = [ReadOnly]
    pagination_class = Pages
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"

    def get_queryset(self):
        # A host the caller can't see is no host at all - its feedback included.
        return super().get_queryset().exclude(title__startswith="hidden")


class PostViewSet(_Host):
    exempt_from_registry = TEST_VIEWSET
    model = Post
    endpoint = "posts"
    serializer_class = PostSerializer


class ThreadViewSet(_Host):
    exempt_from_registry = TEST_VIEWSET
    model = Thread
    endpoint = "threads"
    serializer_class = ThreadSerializer


class ModeratedPostViewSet(PostViewSet):
    exempt_from_registry = TEST_VIEWSET
    endpoint = "moderated-posts"

    def can_moderate_comment(self, request, comment):
        return request.user.username == "moderator" and comment.body != "kept"


class ArticleSerializer(BaseModelSerializer):
    exempt_from_registry = TEST_SERIALIZER

    class Meta:
        model = Article
        fields = ["id", "title"]


class ArticleViewSet(CommentActions, BaseModelViewSet):
    """Comments that aren't threaded."""

    exempt_from_registry = TEST_VIEWSET
    model = Article
    endpoint = "articles"
    serializer_class = ArticleSerializer
    permission_classes = [ReadOnly]
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"


class MultiVoteSerializer(BaseModelSerializer):
    exempt_from_registry = TEST_SERIALIZER

    class Meta:
        model = MultiVoteHost
        fields = ["id"]


class HelpfulnessViewSet(VoteActions, BaseModelViewSet):
    """A host with two votes() - `vote_field` picks one."""

    exempt_from_registry = TEST_VIEWSET
    model = MultiVoteHost
    endpoint = "answers"
    serializer_class = MultiVoteSerializer
    permission_classes = [ReadOnly]
    idempotency_claim_model = "idempotency_by_reference.IdempotencyClaim"
    vote_field = "helpfulness"


class UnpickedViewSet(HelpfulnessViewSet):
    exempt_from_registry = TEST_VIEWSET
    endpoint = "unpicked"
    vote_field = None
