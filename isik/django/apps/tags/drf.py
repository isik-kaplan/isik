"""generic_tag_field()/generic_tag_serializer() - see their own docstrings."""

from rest_framework import serializers

from isik.django.drf.serializers.base import BaseModelSerializer
from isik.django.drf.utils.related_count import ModelRelatedCountField


def generic_tag_field(field):
    """
    Inline DRF field for a host's own serializer - reads/writes tags as a plain list of name
    strings, deduping against `field`'s Tag model on write (via `TagManager.get_tag`, applying
    `normalize=` if configured).

        class PostSerializer(BaseModelSerializer):
            topics = generic_tag_field(Post.topics)

            class Meta:
                model = Post
                fields = ["id", "title", "topics"]
    """
    tag_model = field.model
    max_length = tag_model._meta.get_field("name").max_length

    class _TagNameField(serializers.CharField):
        def run_validation(self, data=serializers.empty):
            # Validators (e.g. max_length) must run against the plain string, not the Tag
            # instance - only convert to a Tag *after* super() has fully validated it.
            name = super().run_validation(data)
            return tag_model.objects.get_tag(name)

    class _TagListField(serializers.ListField):
        child = _TagNameField(max_length=max_length)

        def to_representation(self, value):
            return value.to_list()

    return _TagListField()


def generic_tag_serializer(field):
    """
    Standalone `ModelSerializer` for the Tag model behind `field` (e.g. `Post.topics`) - `id`,
    `name`, and a read-only `usage_count` (how many hosts currently carry this tag). Meant for a
    browse/autocomplete `ModelViewSet`, not for editing a host's own tags inline - see
    `generic_tag_field()` for that.

        TagSerializer = generic_tag_serializer(Post.topics)
    """
    tag_model = field.model
    meta_attrs = {"model": tag_model, "fields": ["id", "name", "usage_count"]}
    meta = type("Meta", (), meta_attrs)  # pragma: no mutate
    # This type's own __name__ ("Meta") is never inspected by Django/DRF - only the "Meta" *key*
    # in attrs below matters (getattr(cls, "Meta")), so the literal name argument here is inert.
    attrs = {"Meta": meta, "usage_count": ModelRelatedCountField(related_name=field.config.related_name)}
    return type(f"{tag_model.__name__}Serializer", (BaseModelSerializer,), attrs)
