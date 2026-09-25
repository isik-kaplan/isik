"""generic_bookmark_serializer() - see its own docstring."""

from isik.django.drf.serializers.base import BaseModelSerializer


def generic_bookmark_serializer(model, name=None):
    """
    Builds a default `ModelSerializer` for a generated `<Host>Bookmark` model - `id`,
    `created_at`, read-only `user`. Use as-is or subclass further.

        BookmarkSerializer = generic_bookmark_serializer(Post.bookmarks.model)

    `name=` overrides the generated class name (default `<Model>Serializer`).
    """

    meta_attrs = {"model": model, "fields": ["id", "created_at", "user"], "read_only_fields": ["user", "created_at"]}
    meta = type("Meta", (), meta_attrs)  # pragma: no mutate
    return type(name or f"{model.__name__}Serializer", (BaseModelSerializer,), {"Meta": meta})
