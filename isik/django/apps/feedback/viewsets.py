"""
VoteActions, BookmarkActions, CommentActions and NoteActions - routes on a host's viewset for its
feedback. See each one's own docstring, and `FeedbackActions` for what they share.
"""

from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import makes_own_exemptions
from isik.django.apps.common._model_makers import attached
from isik.django.apps.feedback.annotations import summary, with_feedback
from isik.django.apps.feedback.bookmarks._bookmarks import _BookmarksField
from isik.django.apps.feedback.comments._comments import _CommentsField
from isik.django.apps.feedback.comments.drf import generic_comment_serializer
from isik.django.apps.feedback.drf import BookmarkSummarySerializer, VoteSummarySerializer
from isik.django.apps.feedback.notes._notes import _NotesField
from isik.django.apps.feedback.notes.drf import generic_note_serializer
from isik.django.apps.feedback.votes._votes import _VotesField
from isik.django.apps.idempotency.drf import NoIdempotencyKey, NoReplay
from isik.django.drf.serializers.base import BaseModelSerializer
from isik.django.drf.viewsets.preconditions import NoPrecondition


@makes_own_exemptions
def _own_exemptions():
    # isik's own, recorded at this line and left out of a project's listing and budgets.
    return {
        "precondition": NoPrecondition(
            reason="writes the caller's own feedback row, never the host row an If-Match would name"
        ),
        "key": NoIdempotencyKey(
            reason="sets the caller's own row to the state the request names, so a retry changes nothing"
        ),
        "replay": NoReplay(
            reason="answers with a feedback row, which a replay by reference to the host can't serialize again"
        ),
    }


_EXEMPT = _own_exemptions()


def _path_parameter(name, description):
    return OpenApiParameter(name, str, OpenApiParameter.PATH, description=description)


class VoteInputSerializer(serializers.Serializer):
    """What `VoteActions`' PUT takes."""

    value = serializers.ChoiceField(choices=[1, -1])


class FeedbackActions:
    """
    What `VoteActions`, `BookmarkActions`, `CommentActions` and `NoteActions` share. Each action fetches
    the host with the view's own `get_object()`, so a row its queryset doesn't hold is a 404 - the host
    queryset decides what can be voted on, bookmarked, commented on or noted.

    The actions are checked with `feedback_permission_classes` (default `IsAuthenticatedOrReadOnly`)
    in place of the view's own `permission_classes`, which are about writing the host - voting on a
    post isn't editing it. Add the host's read permissions there if its queryset doesn't scope it.

    Each mixin picks its attachment by its `<kind>_field` (`vote_field = "upvotes"`), needed only when
    the host has more than one of that kind. On a `BaseModelViewSet` the actions are exempt from
    `If-Match` (they never write the host row); setting the caller's own vote or bookmark, and editing
    or deleting a comment or note, are exempt from the `Idempotency-Key` (a retry changes nothing); and
    creating a comment or note takes a key but isn't replayed (its response isn't the host's). A
    view's own entry for an action wins over these.

    `list` and `retrieve` are annotated with `with_feedback()` for each attachment the view has a
    mixin for, so `VotesField` and the other fields read without a query per row.
    """

    feedback_permission_classes = (IsAuthenticatedOrReadOnly,)
    # Set by each mixin: its attachment's class, the attribute picking one, and
    # {action: (exempt from the idempotency key, never replayed)}.
    feedback_kind = None
    feedback_option = None
    feedback_actions = {}
    schema_list_actions = ()
    schema_actions = {}
    # The write guards' own, for a view that hasn't them - the mixins merge their exemptions into these.
    precondition_exempt_actions = {}
    idempotency_exempt_actions = {}
    idempotency_no_replay_actions = {}

    def __init_subclass__(cls, **kwargs):
        own = {}
        for klass in reversed(cls.__mro__):
            own.update(vars(klass).get("feedback_actions", {}))
        cls.schema_actions = {
            **{
                name: spec
                for klass in reversed(cls.__mro__)
                for name, spec in vars(klass).get("feedback_schema", {}).items()
            },
            **cls.schema_actions,
        }
        cls.schema_list_actions = tuple(
            dict.fromkeys(
                [
                    *(name for klass in cls.__mro__ for name in vars(klass).get("feedback_list_actions", ())),
                    *cls.schema_list_actions,
                ]
            )
        )
        cls.precondition_exempt_actions = {
            **dict.fromkeys(own, _EXEMPT["precondition"]),
            **cls.precondition_exempt_actions,
        }
        cls.idempotency_exempt_actions = {
            **{name: _EXEMPT["key"] for name, (exempt, _replay) in own.items() if exempt},
            **cls.idempotency_exempt_actions,
        }
        cls.idempotency_no_replay_actions = {
            **{name: _EXEMPT["replay"] for name, (_exempt, never) in own.items() if never},
            **cls.idempotency_no_replay_actions,
        }
        super().__init_subclass__(**kwargs)

    @classmethod
    def _feedback_kinds(cls):
        """`(attachment class, option)` for each feedback mixin the view has."""
        return [
            (vars(klass)["feedback_kind"], vars(klass)["feedback_option"])
            for klass in cls.__mro__
            if vars(klass).get("feedback_kind") is not None
        ]

    def _own_feedback_actions(self):
        return {name for klass in type(self).__mro__ for name in vars(klass).get("feedback_actions", {})}

    def feedback_attachment(self, kind, option, model=None):
        """The view's host's `kind` attachment - the one `option` names, else its only one."""
        model = model or self.get_queryset().model
        found = {descriptor.config.attname: descriptor.config for descriptor in attached(model, kind)}
        if not found:
            raise ImproperlyConfigured(
                _("%(view)s's routes need a %(maker)s on %(model)s, which has none.")
                % {"view": type(self).__name__, "maker": kind.maker, "model": model.__name__}
            )
        name = getattr(self, option)
        if name is None and len(found) == 1:
            return next(iter(found.values()))
        if name is not None and name in found:
            return found[name]
        raise ImproperlyConfigured(
            _("%(view)s can't tell which of %(model)s's %(found)s to use - set %(option)s to one of them.")
            % {
                "view": type(self).__name__,
                "model": model.__name__,
                "found": ", ".join(found),
                "option": option,
            }
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action in ("list", "retrieve"):
            names = [
                self.feedback_attachment(kind, option, queryset.model).attname
                for kind, option in self._feedback_kinds()
            ]
            queryset = with_feedback(queryset, self.request.user, only=names)
        return queryset

    def get_permissions(self):
        if self.action in self._own_feedback_actions():
            return [permission() for permission in self.feedback_permission_classes]
        return super().get_permissions()

    def feedback_serializer(self, config, explicit, factory):
        """`explicit`, else the serializer registered for the attachment's model, else `factory`'s."""
        if explicit is not None:
            return explicit
        try:
            return BaseModelSerializer.get_for_model(config.model)
        except KeyError:
            return factory(config.model)

    def attachment_of(self, spec):
        """The attachment the feedback mixin `spec` serves - see `feedback_attachment()`."""
        return self.feedback_attachment(spec.feedback_kind, spec.feedback_option)

    def require_signed_in(self, request):
        if not request.user.is_authenticated:
            raise NotAuthenticated()


class VoteActions(FeedbackActions):
    """
    `PUT {host}/{pk}/vote/` with `{"value": 1}` or `-1` sets the caller's vote, `DELETE` takes it back;
    both answer with the host's `{score, up, down, mine}`. See `FeedbackActions`.
    """

    feedback_kind = _VotesField
    feedback_option = "vote_field"
    feedback_actions = {"vote": (True, False)}
    feedback_schema = {
        "vote": {
            "PUT": {"request": VoteInputSerializer, "response": VoteSummarySerializer},
            "DELETE": {"request": None, "response": VoteSummarySerializer, "status": 200},
        }
    }
    vote_field = None

    @action(detail=True, methods=["put", "delete"])
    def vote(self, request, *args, **kwargs):
        """Sets (PUT) or takes back (DELETE) the caller's vote, answering with the votes it now has."""
        host = self.get_object()
        config = self.attachment_of(VoteActions)
        mine = config.model._default_manager.filter(**{config.target_name: host, "user": request.user})
        if request.method == "PUT":
            given = VoteInputSerializer(data=request.data)
            given.is_valid(raise_exception=True)
            mine.update_or_create(
                **{config.target_name: host, "user": request.user}, defaults={"value": given.validated_data["value"]}
            )
        else:
            mine.delete()
        return Response(summary(config, host, request.user))

    def get_serializer_class(self):
        if self.action == "vote":
            return VoteInputSerializer
        return super().get_serializer_class()


class BookmarkActions(FeedbackActions):
    """
    `PUT {host}/{pk}/bookmark/` bookmarks it for the caller, `DELETE` takes it back; both answer with
    the host's `{count, mine}`. See `FeedbackActions`.
    """

    feedback_kind = _BookmarksField
    feedback_option = "bookmark_field"
    feedback_actions = {"bookmark": (True, False)}
    feedback_schema = {
        "bookmark": {
            "PUT": {"request": None, "response": BookmarkSummarySerializer},
            "DELETE": {"request": None, "response": BookmarkSummarySerializer, "status": 200},
        }
    }
    bookmark_field = None

    @action(detail=True, methods=["put", "delete"])
    def bookmark(self, request, *args, **kwargs):
        """Bookmarks it (PUT) or takes the bookmark back (DELETE), answering with its bookmarks."""
        host = self.get_object()
        config = self.attachment_of(BookmarkActions)
        lookup = {config.target_name: host, "user": request.user}
        if request.method == "PUT":
            config.model._default_manager.get_or_create(**lookup)
        else:
            config.model._default_manager.filter(**lookup).delete()
        return Response(summary(config, host, request.user))

    def get_serializer_class(self):
        if self.action == "bookmark":
            return BookmarkSummarySerializer
        return super().get_serializer_class()


class _RowActions(FeedbackActions):
    """A host's comments or notes as rows of their own: a page of them, a new one, and one edited or
    deleted - by its author. A view can have both, so each step runs the hooks of the mixin whose
    action it serves (`spec`), never whichever comes first in the view's MRO."""

    # The action names, the serializer option, and the serializer factory - set by each.
    row_list_action = None
    row_action = None
    row_serializer_option = None
    row_serializer_factory = None

    @classmethod
    def _row_specs(cls):
        return [klass for klass in cls.__mro__ if vars(klass).get("row_list_action") is not None]

    def row_serializer(self, spec):
        explicit = getattr(self, spec.row_serializer_option)
        return self.feedback_serializer(self.attachment_of(spec), explicit, spec.row_serializer_factory)

    @staticmethod
    def rows_of(request, config, host):
        """The rows `request`'s caller can see of `host` - every one, for comments."""
        return config.model._default_manager.filter(**{config.target_name: host})

    def may_change(self, request, row):
        """Whether the caller may edit or delete `row` - its author."""
        return row.user_id == request.user.pk

    @staticmethod
    def listed(request, config, rows):
        """`rows` narrowed by `request`'s query - all of them, unless a mixin says otherwise."""
        return rows

    @staticmethod
    def edit_stamp(config):
        """What an edit saves beside its fields."""
        return {}

    def get_serializer_class(self):
        for spec in self._row_specs():
            if self.action in (spec.row_list_action, spec.row_action):
                return self.row_serializer(spec)
        return super().get_serializer_class()

    def rows_response(self, spec, request, host):
        config = self.attachment_of(spec)
        serializer_class = self.row_serializer(spec)
        context = {**self.get_serializer_context(), "target": host}
        if request.method == "POST":
            self.require_signed_in(request)
            serializer = serializer_class(data=request.data, context=context)
            serializer.is_valid(raise_exception=True)
            serializer.save(user=request.user, **{config.target_name: host})
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        rows = spec.listed(request, config, spec.rows_of(request, config, host)).order_by("created_at", "pk")
        page = self.paginate_queryset(rows)
        if page is not None:
            return self.get_paginated_response(serializer_class(page, many=True, context=context).data)
        return Response(serializer_class(rows, many=True, context=context).data)

    def row_response(self, spec, request, host, row_pk):
        config = self.attachment_of(spec)
        self.require_signed_in(request)
        row = get_object_or_404(spec.rows_of(request, config, host), pk=row_pk)
        if not spec.may_change(self, request, row):
            raise PermissionDenied(_("Only its author can change it."))
        if request.method == "DELETE":
            row.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        context = {**self.get_serializer_context(), "target": host}
        serializer = self.row_serializer(spec)(row, data=request.data, partial=True, context=context)
        serializer.is_valid(raise_exception=True)
        if "parent" in serializer.validated_data:
            raise ValidationError({"parent": [_("A comment can't be moved under another one.")]})
        serializer.save(**spec.edit_stamp(config))
        return Response(serializer.data)


class CommentActions(_RowActions):
    """
    `GET {host}/{pk}/comments/` - a page of its comments, oldest first, each with `parent` and
    `reply_count` when threaded (`?parent=<comment>` for one comment's replies, `?top_level=true` for
    the comments replying to none); `POST` adds one, as a reply when it names a `parent`.
    `PATCH`/`DELETE {host}/{pk}/comments/{comment_pk}/` edit or delete one - its author's, or one
    `can_moderate_comment()` allows. Its serializer is `comment_serializer_class`, else the one
    registered for the comment model, else `generic_comment_serializer()`'s. See `FeedbackActions`.
    """

    feedback_kind = _CommentsField
    feedback_option = "comment_field"
    feedback_actions = {"comments": (False, True), "comment": (True, False)}
    feedback_list_actions = ("comments",)
    feedback_schema = {
        "comments": {
            "GET": {
                "parameters": [
                    OpenApiParameter("parent", str, description="Only the replies to this comment."),
                    OpenApiParameter("top_level", bool, description="Only the comments replying to none."),
                ]
            },
            "POST": {"status": 201},
        },
        "comment": {"parameters": [_path_parameter("comment_pk", "The comment's id.")]},
    }
    comment_field = None
    comment_serializer_class = None
    row_list_action = "comments"
    row_action = "comment"
    row_serializer_option = "comment_serializer_class"
    row_serializer_factory = staticmethod(generic_comment_serializer)

    def can_moderate_comment(self, request, comment):
        """Whether the caller may edit or delete another's comment - nobody, unless overridden."""
        return False

    def may_change(self, request, row):
        return row.user_id == request.user.pk or self.can_moderate_comment(request, row)

    @staticmethod
    def listed(request, config, rows):
        parent, top_level = request.query_params.get("parent"), request.query_params.get("top_level")
        if (parent is not None or top_level is not None) and not config.is_threaded:
            raise ValidationError({"parent": [_("These comments aren't threaded.")]})
        if config.is_threaded:
            rows = config.with_reply_counts(rows)
        if parent is not None:
            try:
                parent = config.model._meta.pk.to_python(parent)
            except DjangoValidationError as error:
                raise ValidationError({"parent": error.messages}) from None
            rows = rows.filter(parent=parent)
        if top_level in ("true", "1"):
            rows = rows.filter(parent__isnull=True)
        return rows

    @staticmethod
    def edit_stamp(config):
        return {"edited_at": timezone.now()} if config.tracks_edits else {}

    @action(detail=True, methods=["get", "post"])
    def comments(self, request, *args, **kwargs):
        """Its comments, oldest first (GET), or a new one (POST)."""
        return self.rows_response(CommentActions, request, self.get_object())

    @action(detail=True, methods=["patch", "delete"], url_path=r"comments/(?P<comment_pk>[^/.]+)")
    def comment(self, request, *args, **kwargs):
        """Edits (PATCH) or deletes (DELETE) one of its comments - the caller's own."""
        return self.row_response(CommentActions, request, self.get_object(), kwargs["comment_pk"])


class NoteActions(_RowActions):
    """
    `GET {host}/{pk}/notes/` - a page of the caller's own notes on it, oldest first; `POST` adds one.
    `PATCH`/`DELETE {host}/{pk}/notes/{note_pk}/` edit or delete one of them. Notes are private: every
    route needs a signed-in caller and sees only their own. Its serializer is `note_serializer_class`,
    else the one registered for the note model, else `generic_note_serializer()`'s. See
    `FeedbackActions`.
    """

    feedback_kind = _NotesField
    feedback_option = "note_field"
    feedback_actions = {"notes": (False, True), "note": (True, False)}
    feedback_list_actions = ("notes",)
    feedback_schema = {
        "notes": {"POST": {"status": 201}},
        "note": {"parameters": [_path_parameter("note_pk", "The note's id.")]},
    }
    note_field = None
    note_serializer_class = None
    row_list_action = "notes"
    row_action = "note"
    row_serializer_option = "note_serializer_class"
    row_serializer_factory = staticmethod(generic_note_serializer)

    @staticmethod
    def rows_of(request, config, host):
        return config.model._default_manager.filter(**{config.target_name: host, "user": request.user})

    @action(detail=True, methods=["get", "post"])
    def notes(self, request, *args, **kwargs):
        """The caller's own notes on it, oldest first (GET), or a new one (POST)."""
        self.require_signed_in(request)
        return self.rows_response(NoteActions, request, self.get_object())

    @action(detail=True, methods=["patch", "delete"], url_path=r"notes/(?P<note_pk>[^/.]+)")
    def note(self, request, *args, **kwargs):
        """Edits (PATCH) or deletes (DELETE) one of the caller's own notes on it."""
        return self.row_response(NoteActions, request, self.get_object(), kwargs["note_pk"])
