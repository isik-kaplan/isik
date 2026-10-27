"""generic_tag_field()/generic_tag_serializer()/TagUsageField - see their own docstrings."""

from rest_framework import serializers

from isik.django.apps.tags.tags import with_tag_usage
from isik.django.drf.serializers.base import BaseModelSerializer


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


class TagUsageField(serializers.IntegerField):
    """How many of `field`'s host rows carry a tag - the `usage_count` `with_tag_usage()` annotates,
    else counted for that tag alone."""

    def __init__(self, field, **kwargs):
        self.tagged = field
        super().__init__(**{**kwargs, "source": "*", "read_only": True})

    def __deepcopy__(self, memo):
        # A serializer copies its fields for each instance - the field it counts is a model's, never copied.
        return type(self)(self.tagged, **self._kwargs)

    def to_representation(self, value):
        if hasattr(value, "usage_count"):
            return value.usage_count
        return with_tag_usage(type(value)._default_manager.filter(pk=value.pk), self.tagged).get().usage_count


def generic_tag_serializer(field, name=None):
    """
    Standalone `ModelSerializer` for the Tag model behind `field` (e.g. `Post.topics`) - `id`,
    `name`, and a read-only `usage_count` (how many hosts currently carry this tag - read from
    `with_tag_usage()`, so annotate the view's queryset with it for a list in one query). Meant for a
    browse/autocomplete `ModelViewSet`, not for editing a host's own tags inline - see
    `generic_tag_field()` for that.

        TagSerializer = generic_tag_serializer(Post.topics)

    `name=` overrides the generated class name (default `<TagModel>Serializer`).
    """
    tag_model = field.model
    meta_attrs = {"model": tag_model, "fields": ["id", "name", "usage_count"]}
    meta = type("Meta", (), meta_attrs)
    attrs = {"Meta": meta, "usage_count": TagUsageField(field)}
    return type(name or f"{tag_model.__name__}Serializer", (BaseModelSerializer,), attrs)
