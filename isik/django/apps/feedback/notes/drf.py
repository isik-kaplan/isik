"""generic_note_serializer() - see its own docstring."""

from isik.django.drf.serializers.base import BaseModelSerializer


def generic_note_serializer(model, name=None):
    """
    Builds a default `ModelSerializer` for a generated `<Host>Note` model - `id`, `body`,
    `created_at`, `updated_at`, read-only `user`. `body` is writable, meant to back a
    `ModelViewSet` scoped to the requesting user's own rows - `isik.django.drf.permissions.is_owner`
    covers the object-level actions (retrieve/update/destroy); list/create still need the view's
    own `get_queryset()` filter and `perform_create()` to set `user`.

        NoteSerializer = generic_note_serializer(Post.notes.model)

    `name=` overrides the generated class name (default `<Model>Serializer`).
    """

    meta_attrs = {
        "model": model,
        "fields": ["id", "body", "created_at", "updated_at", "user"],
        "read_only_fields": ["user", "created_at", "updated_at"],
    }
    meta = type("Meta", (), meta_attrs)  # pragma: no mutate
    return type(name or f"{model.__name__}Serializer", (BaseModelSerializer,), {"Meta": meta})
