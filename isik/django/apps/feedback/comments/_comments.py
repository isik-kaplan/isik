"""
comments() maker + UserCommentMixin - see `comments` for usage. Tiptap validation lives in
`comments.tiptap`, imported lazily so plain-text comments never require `prosemirror` installed.
"""

from django.conf import settings
from django.core.validators import MinLengthValidator
from django.db import models

from isik.django.apps.common._model_makers import (
    build_model,
    claim_related_name,
    expose_via_reverse_accessor,
    resolve_base_model,
    resolve_field,
)


class _CommentsField:
    """Descriptor returned by `comments()` - see `comments` for the public docstring."""

    # tiptap/comment_min_length/comment_max_length/tiptap_schema_path have no default here - like
    # target_name/target_related_name, _CommentsField is only ever built by the comments() maker
    # below, which always forwards its own (already-defaulted) values explicitly.
    def __init__(
        self,
        *,
        user_related_name,
        target_name,
        target_related_name,
        user_model=None,
        base_model=None,
        extra_fields=None,
        tiptap,
        comment_min_length,
        comment_max_length,
        tiptap_schema_path,
    ):
        self.user_related_name = user_related_name
        self.target_name = target_name
        self.target_related_name = target_related_name
        self.user_model = user_model or settings.AUTH_USER_MODEL
        self.base_model = base_model
        self.extra_fields = extra_fields or {}
        self.tiptap = tiptap
        self.comment_min_length = comment_min_length
        self.comment_max_length = comment_max_length
        self.tiptap_schema_path = tiptap_schema_path

    def _body_field(self):
        if self.tiptap:
            from isik.django.apps.feedback.comments.tiptap import TiptapValidator

            validator = TiptapValidator(
                min_length=self.comment_min_length,
                max_length=self.comment_max_length,
                schema_path=self.tiptap_schema_path,
            )
            return models.JSONField(validators=[validator])

        # CharField, not TextField(validators=[MaxLengthValidator(...)]) - same reasoning as
        # notes()'s body_max_length: only CharField gets a DB-level varchar(N) column, so a caller
        # bypassing full_clean() (e.g. UserCommentMixin.comment()'s own objects.create()) still
        # can't insert an over-long body.
        validators = [MinLengthValidator(self.comment_min_length)]
        if self.comment_max_length:
            return models.CharField(max_length=self.comment_max_length, validators=validators)
        return models.TextField(validators=validators)

    def contribute_to_class(self, host_cls, name):
        model_name = f"{host_cls.__name__}{name.capitalize()}Comment"
        claim_related_name(host_cls, self.target_related_name, model_name)
        claim_related_name(self.user_model, self.user_related_name, model_name)

        fields = {
            self.target_name: models.ForeignKey(
                host_cls, on_delete=models.CASCADE, related_name=self.target_related_name
            ),
            "user": models.ForeignKey(self.user_model, on_delete=models.CASCADE, related_name=self.user_related_name),
            "body": self._body_field(),
            **self.extra_fields,
        }
        generated_model = build_model(
            model_name,
            host_cls,
            fields=fields,
            base_model=resolve_base_model(self.base_model, "FEEDBACK_COMMENTS_BASE_MODEL"),
        )
        expose_via_reverse_accessor(
            host_cls, name, self.target_related_name, generated_model=generated_model, config=self
        )


def comments(
    *,
    user_related_name,
    target_name="target",
    target_related_name="comments",
    user_model=None,
    base_model=None,
    extra_fields=None,
    tiptap=False,
    comment_min_length=1,
    comment_max_length=None,
    tiptap_schema_path=None,
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
    """
    return _CommentsField(
        user_related_name=user_related_name,
        target_name=target_name,
        target_related_name=target_related_name,
        user_model=user_model,
        base_model=base_model,
        extra_fields=extra_fields,
        tiptap=tiptap,
        comment_min_length=comment_min_length,
        comment_max_length=comment_max_length,
        tiptap_schema_path=tiptap_schema_path,
    )


class UserCommentMixin:
    """Mix into your User model to comment on anything with `comments()` attached - see `comments`."""

    def comment(self, obj, body, *, field=None):
        field = resolve_field(obj, field, _CommentsField, "commentable")
        return field.model.objects.create(**{field.config.target_name: obj, "user": self, "body": body})

    def comments_on(self, obj, *, field=None):
        field = resolve_field(obj, field, _CommentsField, "commentable")
        return field.model.objects.filter(**{field.config.target_name: obj})
