"""generic_bookmark_serializer() - see its own docstring."""

from isik.django.drf.serializers.base import BaseModelSerializer


def generic_bookmark_serializer(model):
    """
    Builds a default `ModelSerializer` for a generated `<Host>Bookmark` model - `id`,
    `created_at`, read-only `user`. Use as-is or subclass further.

        BookmarkSerializer = generic_bookmark_serializer(Post.bookmark_model)
    """

    meta_attrs = {"model": model, "fields": ["id", "created_at", "user"], "read_only_fields": ["user", "created_at"]}
    meta = type("Meta", (), meta_attrs)
    return type(f"{model.__name__}Serializer", (BaseModelSerializer,), {"Meta": meta})
