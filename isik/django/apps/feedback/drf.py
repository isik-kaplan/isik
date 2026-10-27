"""
Serializer fields for a host's feedback - VotesField, BookmarksField, CommentCountField and NoteCountField -
reading what `with_feedback()` annotates. See each one's own docstring.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.django.apps.feedback.annotations import summary
from isik.django.apps.feedback.bookmarks._bookmarks import _BookmarksField
from isik.django.apps.feedback.comments._comments import _CommentsField
from isik.django.apps.feedback.notes._notes import _NotesField
from isik.django.apps.feedback.votes._votes import _VotesField


class VoteSummarySerializer(serializers.Serializer):
    """What `VotesField` reads as - and what `VoteActions` answers with."""

    score = serializers.IntegerField(help_text=gettext_lazy("Upvotes minus downvotes."))
    up = serializers.IntegerField(help_text=gettext_lazy("How many upvoted it."))
    down = serializers.IntegerField(help_text=gettext_lazy("How many downvoted it."))
    mine = serializers.IntegerField(
        allow_null=True, help_text=gettext_lazy("The caller's own vote - 1, -1, or null for none.")
    )


class BookmarkSummarySerializer(serializers.Serializer):
    """What `BookmarksField` reads as - and what `BookmarkActions` answers with."""

    count = serializers.IntegerField(help_text=gettext_lazy("How many bookmarked it."))
    mine = serializers.BooleanField(help_text=gettext_lazy("Whether the caller bookmarked it."))


class _AnnotatedField(serializers.Field):
    """A host row's annotations from one attachment, read off the row - or, for a row the queryset
    didn't annotate (a create's response), fetched for it alone."""

    attachment_class = None
    maker = None

    def __init__(self, field, **kwargs):
        if not isinstance(getattr(field, "config", None), self.attachment_class):
            raise TypeError(
                _("%(field)s takes a %(maker)s attachment - Host.<name> - not %(value)r")
                % {"field": type(self).__name__, "maker": self.maker, "value": field}
            )
        self.field = field
        self.attachment = field.config
        super().__init__(**{**kwargs, "source": "*", "read_only": True})

    def __deepcopy__(self, memo):
        # A serializer copies its fields for each instance - the attachment is the host's, never copied.
        return type(self)(self.field, **self._kwargs)

    def annotated(self, row):
        """`{what: value}` - see `summary()` - as the request's user."""
        return summary(self.attachment, row, getattr(self.context.get("request"), "user", None))


@extend_schema_field(VoteSummarySerializer)
class VotesField(_AnnotatedField):
    """
    A host's votes as `{score, up, down, mine}`, read from `with_feedback()`'s annotations:

        class PostSerializer(BaseModelSerializer):
            votes = VotesField(Post.votes)
    """

    attachment_class = _VotesField
    maker = "votes()"

    def to_representation(self, value):
        return self.annotated(value)


@extend_schema_field(BookmarkSummarySerializer)
class BookmarksField(_AnnotatedField):
    """A host's bookmarks as `{count, mine}` - see `VotesField`."""

    attachment_class = _BookmarksField
    maker = "bookmarks()"

    def to_representation(self, value):
        return self.annotated(value)


@extend_schema_field(OpenApiTypes.INT)
class CommentCountField(_AnnotatedField):
    """How many comments a host has - see `VotesField`."""

    attachment_class = _CommentsField
    maker = "comments()"

    def to_representation(self, value):
        return self.annotated(value)["count"]


@extend_schema_field(OpenApiTypes.INT)
class NoteCountField(_AnnotatedField):
    """How many notes the caller has on a host - notes are private - see `VotesField`."""

    attachment_class = _NotesField
    maker = "notes()"

    def to_representation(self, value):
        return self.annotated(value)["mine_count"]
