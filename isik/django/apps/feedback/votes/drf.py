"""generic_vote_serializer() - see its own docstring."""

from isik.django.drf.serializers.base import BaseModelSerializer


def generic_vote_serializer(model):
    """
    Builds a default `ModelSerializer` for a generated `<Host>Vote` model - `id`, `value`,
    `created_at`, read-only `user` (never client-writable - the view must still set it, e.g. in
    `perform_create`). Use as-is or subclass further.

        VoteSerializer = generic_vote_serializer(Post.votes.model)
    """

    meta_attrs = {
        "model": model,
        "fields": ["id", "value", "created_at", "user"],
        "read_only_fields": ["user", "created_at"],
    }
    meta = type("Meta", (), meta_attrs)
    return type(f"{model.__name__}Serializer", (BaseModelSerializer,), {"Meta": meta})
