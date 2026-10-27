"""generic_comment_serializer() - see its own docstring."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from isik.django.apps.feedback._attachment import config_of, has_field
from isik.django.apps.feedback.comments._comments import check_parent
from isik.django.drf.serializers.base import BaseModelSerializer


class ReplyCountField(serializers.IntegerField):
    """A comment's direct replies - the `reply_count` `with_reply_counts()` annotates, else counted."""

    def __init__(self, **kwargs):
        super().__init__(**{**kwargs, "source": "*", "read_only": True})

    def to_representation(self, value):
        if hasattr(value, "reply_count"):
            return value.reply_count
        return type(value)._default_manager.filter(parent=value).count()


class _ThreadedCommentMixin:
    """A reply's `parent` is checked against the target the comment goes on - `context["target"]`."""

    def validate_parent(self, parent):
        if parent is not None:
            try:
                check_parent(config_of(self.Meta.model), parent, self.context["target"].pk)
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.messages) from None
        return parent


def generic_comment_serializer(model, name=None):
    """
    Builds a default `ModelSerializer` for a generated `<Host>Comment` model - `id`, `body`,
    `created_at`, `updated_at`, read-only `user`. `body`'s serializer field is derived from
    whatever the model field actually is (`TextField`/`CharField` for plain text, `JSONField` for
    `comments(tiptap=True)`), so model-level validators - including `TiptapValidator` - carry over
    automatically. Use as-is or subclass further.

        CommentSerializer = generic_comment_serializer(Post.comments.model)

    A threaded model - one with a `parent` - adds `parent`, checked against the target the comment goes
    on (`context["target"]`) and the attachment's `max_depth`, and a read-only `reply_count`; one with an
    `edited_at` adds it, read-only.

    `name=` overrides the generated class name (default `<Model>Serializer`).
    """

    fields = ["id", "body", "created_at", "updated_at", "user"]
    read_only = ["user", "created_at", "updated_at"]
    attrs = {}
    bases = (BaseModelSerializer,)
    if has_field(model, "parent"):
        fields += ["parent", "reply_count"]
        attrs["reply_count"] = ReplyCountField()
        bases = (_ThreadedCommentMixin, BaseModelSerializer)
    if has_field(model, "edited_at"):
        fields.append("edited_at")
        read_only.append("edited_at")
    meta = type("Meta", (), {"model": model, "fields": fields, "read_only_fields": read_only})
    return type(name or f"{model.__name__}Serializer", bases, {**attrs, "Meta": meta})
