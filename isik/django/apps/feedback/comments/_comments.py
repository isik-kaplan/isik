"""
comments() maker + UserCommentMixin - see `comments` for usage. Tiptap validation lives in
`comments.tiptap`, imported lazily so plain-text comments never require `prosemirror` installed.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy, lazy_format
from isik.django.apps.common._contracts import Need, kind, points_at
from isik.django.apps.common._model_makers import resolve_field
from isik.django.apps.feedback._attachment import FeedbackAttachment, config_of, has_field


class _CommentsField(FeedbackAttachment):
    """Descriptor returned by `comments()` - see `comments` for the public docstring."""

    maker = "comments()"
    noun = "Comment"
    base_setting = "FEEDBACK_COMMENTS_BASE_MODEL"
    default_related_name = "comments"
    timestamps = ("created_at", "updated_at")

    def __init__(self, *, max_depth, **keywords):
        super().__init__(**keywords)
        generating = keywords["generating"]
        self.tiptap = generating["tiptap"]
        self.comment_min_length = 1 if generating["comment_min_length"] is None else generating["comment_min_length"]
        self.comment_max_length = generating["comment_max_length"]
        self.tiptap_schema_path = generating["tiptap_schema_path"]
        self.threaded = generating["threaded"]
        self.track_edits = generating["track_edits"]
        self.max_depth = max_depth

    def descriptions(self, host_cls):
        return (
            (
                lazy_format("The %(target)s this comment is on.", target=host_cls.__name__),
                f"The {host_cls.__name__} this comment is on.",
            ),
            (gettext_lazy("Who wrote this comment."), "Who wrote this comment."),
        )

    def own_fields(self):
        fields = {"body": self._body_field()}
        if self.threaded:
            fields["parent"] = models.ForeignKey(
                "self",
                null=True,
                blank=True,
                on_delete=models.CASCADE,
                related_name="replies",
                help_text=gettext_lazy("The comment this one replies to - empty for a top-level comment."),
                db_comment="The comment this one replies to - null for a top-level comment.",
            )
        if self.track_edits:
            fields["edited_at"] = models.DateTimeField(
                null=True,
                blank=True,
                help_text=gettext_lazy("When its author last edited it - empty if never."),
                db_comment="When its author last edited it - null if never.",
            )
        return fields

    def _body_field(self):
        if self.tiptap:
            from isik.django.apps.feedback.comments.tiptap import TiptapValidator

            validator = TiptapValidator(
                min_length=self.comment_min_length,
                max_length=self.comment_max_length,
                schema_path=self.tiptap_schema_path,
            )
            return models.JSONField(
                validators=[validator],
                help_text=gettext_lazy("The comment, as a Tiptap document."),
                db_comment="The comment, as a Tiptap document.",
            )

        # CharField, not TextField(validators=[MaxLengthValidator(...)]) - same reasoning as
        # notes()'s body_max_length: only CharField gets a DB-level varchar(N) column, so a caller
        # bypassing full_clean() (e.g. UserCommentMixin.comment()'s own objects.create()) still
        # can't insert an over-long body.
        validators = [MinLengthValidator(self.comment_min_length)]
        described = {"help_text": gettext_lazy("The comment's text."), "db_comment": "The comment's text."}
        if self.comment_max_length:
            return models.CharField(max_length=self.comment_max_length, validators=validators, **described)
        return models.TextField(validators=validators, **described)

    def own_needs(self):
        return (
            kind(
                "body", (models.TextField, models.CharField, models.JSONField), _("a TextField, CharField or JSONField")
            ),
        )

    def optional_needs(self):
        return (
            Need("parent", _("a ForeignKey to the comment model itself"), self._is_parent),
            kind("edited_at", models.DateTimeField, _("a DateTimeField")),
        )

    @staticmethod
    def _is_parent(found):
        return points_at(found, found.model) and found.null

    def own_annotations(self, signed_in, user):
        return (("count", self.count(self.rows())),)

    def with_reply_counts(self, queryset):
        """`queryset` - of its comments - with `reply_count`, each one's direct replies."""
        replies = self.model._default_manager.filter(parent=OuterRef("pk")).order_by()
        return queryset.annotate(
            reply_count=Coalesce(Subquery(replies.values("parent").annotate(n=Count("pk")).values("n")), 0)
        )

    @property
    def is_threaded(self):
        """Whether its comments reply to each other - its model has a `parent`."""
        return has_field(self.host_descriptor.model, "parent")

    @property
    def tracks_edits(self):
        """Whether an edit is stamped - its model has an `edited_at`."""
        return has_field(self.host_descriptor.model, "edited_at")

    @property
    def host_descriptor(self):
        return getattr(self.host, self.attname)


def comments(
    *,
    model=None,
    user_related_name=None,
    target_name=None,
    target_related_name=None,
    user_model=None,
    base_model=None,
    extra_fields=None,
    model_name=None,
    tiptap=None,
    comment_min_length=None,
    comment_max_length=None,
    tiptap_schema_path=None,
    threaded=None,
    track_edits=None,
    max_depth=None,
):
    """
    Attaches a per-host-model comment log, plus `UserCommentMixin` on the User model.

        class Post(models.Model):
            comments = comments(user_related_name="post_comments")

        class User(UserCommentMixin, AbstractUser):
            pass

        user.comment(post, "nice post")
        post.comments.all()   # real Django manager
        Post.comments.model   # the generated PostComment model

    `body` is plain text by default, validated by `comment_min_length`/`comment_max_length`. Pass
    `tiptap=True` to store a Tiptap/ProseMirror JSON document instead - see `comments.tiptap` for
    what that requires and how it's validated; `tiptap_schema_path` overrides
    `FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH` for this call only, in case one comment surface allows
    a different node set than another.

    `votes()` isn't attached automatically - opt in with
    `extra_fields={"votes": votes(user_related_name=...)}`. Same `user_related_name`/
    `target_related_name`/`target_name`/`base_model` knobs as the other makers. A host can attach
    `comments()` more than once - see `votes()`'s docstring for the `field=` disambiguation rule.

    `model_name` overrides the generated model's class name (default `<Host><Attr>Comment`, e.g.
    `Post.comments` -> `PostCommentsComment`). It also names the table and constraints, so settle it
    before the first migration.

    `threaded=True` adds `parent` - the comment one replies to - with replies going at most `max_depth`
    deep (top-level is 0; unset, no limit); `track_edits=True` adds `edited_at`, stamped on each edit.

    `model=` binds a model you wrote instead - a class or `"app_label.Model"`, checked against what
    this reads of it once registered; the options that describe a built model are refused beside it.
    See docs/django/apps/feedback/your_own_models.md.
    """
    return _CommentsField(
        model=model,
        target_name=target_name,
        max_depth=max_depth,
        generating={
            "user_related_name": user_related_name,
            "target_related_name": target_related_name,
            "user_model": user_model,
            "base_model": base_model,
            "extra_fields": extra_fields,
            "model_name": model_name,
            "tiptap": tiptap,
            "comment_min_length": comment_min_length,
            "comment_max_length": comment_max_length,
            "tiptap_schema_path": tiptap_schema_path,
            "threaded": threaded,
            "track_edits": track_edits,
        },
    )


class UserCommentMixin:
    """Mix into your User model to comment on anything with `comments()` attached - see `comments`."""

    def comment(self, obj, body, *, field=None):
        field = resolve_field(obj, field, _CommentsField, "commentable")
        return field.model.objects.create(**{field.config.target_name: obj, "user": self, "body": body})

    def comments_on(self, obj, *, field=None):
        field = resolve_field(obj, field, _CommentsField, "commentable")
        return field.model.objects.filter(**{field.config.target_name: obj})

    def reply(self, comment, body):
        """A reply to `comment`, on the same target - refused past the attachment's `max_depth`."""
        config = config_of(type(comment))
        check_parent(config, comment, getattr(comment, f"{config.target_name}_id"))
        return type(comment).objects.create(
            **{f"{config.target_name}_id": getattr(comment, f"{config.target_name}_id")},
            user=self,
            body=body,
            parent=comment,
        )

    def edit_comment(self, comment, body):
        """Sets `comment`'s body - its author's own only - stamping `edited_at` where the model has one."""
        config = config_of(type(comment))
        self._check_author(comment, _("Only its author can edit a comment."))
        comment.body = body
        if config.tracks_edits:
            comment.edited_at = timezone.now()
        comment.save()
        return comment

    def delete_comment(self, comment):
        """Deletes `comment` - its author's own only - and, on a threaded attachment, its replies."""
        config_of(type(comment))
        self._check_author(comment, _("Only its author can delete a comment."))
        comment.delete()

    def _check_author(self, comment, message):
        if comment.user_id != self.pk:
            raise PermissionDenied(message)


def check_parent(config, parent, target_id):
    """
    Raises `ValidationError` unless `parent` can be replied to on the target `target_id`: the
    attachment is threaded, `parent` is on that target, and a reply to it stays within `max_depth`
    (top-level comments are depth 0). Depth is counted by walking up the parents - nothing stores it.
    """
    if not config.is_threaded:
        raise ValidationError(_("These comments aren't threaded - a comment can't reply to another."))
    if getattr(parent, f"{config.target_name}_id") != target_id:
        raise ValidationError(_("A reply has to be on the same thing as the comment it replies to."))
    if config.max_depth is None:
        return
    depth, above = 1, parent.parent_id
    while above is not None and depth <= config.max_depth:
        depth += 1
        above = type(parent).objects.filter(pk=above).values_list("parent_id", flat=True).first()
    if depth > config.max_depth:
        raise ValidationError(
            _("Replies go %(max_depth)d deep at most - this one would be deeper.") % {"max_depth": config.max_depth}
        )
