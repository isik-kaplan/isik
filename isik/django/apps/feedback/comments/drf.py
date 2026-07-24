"""generic_comment_serializer() - see its own docstring."""

from isik.django.drf.serializers.base import BaseModelSerializer


def generic_comment_serializer(model):
    """
    Builds a default `ModelSerializer` for a generated `<Host>Comment` model - `id`, `body`,
    `created_at`, `updated_at`, read-only `user`. `body`'s serializer field is derived from
    whatever the model field actually is (`TextField`/`CharField` for plain text, `JSONField` for
    `comments(tiptap=True)`), so model-level validators - including `TiptapValidator` - carry over
    automatically. Use as-is or subclass further.

        CommentSerializer = generic_comment_serializer(Post.comment_model)
    """

    meta_attrs = {
        "model": model,
        "fields": ["id", "body", "created_at", "updated_at", "user"],
        "read_only_fields": ["user", "created_at", "updated_at"],
    }
    meta = type("Meta", (), meta_attrs)
    return type(f"{model.__name__}Serializer", (BaseModelSerializer,), {"Meta": meta})
