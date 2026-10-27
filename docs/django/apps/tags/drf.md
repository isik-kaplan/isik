# drf

## generic_tag_field(field)
Inline DRF field for a host's own serializer - reads/writes tags as a plain list of name strings, deduping against `field`'s Tag model on write (via `TagManager.get_tag`, applying `normalize=` if configured).

```python
from isik.django.apps.tags.drf import generic_tag_field

class PostSerializer(BaseModelSerializer):
    topics = generic_tag_field(Post.topics)

    class Meta:
        model = Post
        fields = ["id", "title", "topics"]
```

## generic_tag_serializer(field)
Standalone `ModelSerializer` for the Tag model behind `field` - `id`, `name`, and a read-only `usage_count` (how many hosts currently carry this tag, read from [`with_tag_usage()`](tags.md#with_tag_usagequeryset-field) - annotate the view's queryset with it, or each row is counted on its own). Meant for a browse/autocomplete `ModelViewSet`, not for editing a host's own tags inline - see `generic_tag_field()` for that.

```python
from isik.django.apps.tags.drf import generic_tag_serializer

TagSerializer = generic_tag_serializer(Post.topics)
```

- `generic_tag_serializer(field, name=...)` overrides the generated class name (default `<TagModel>Serializer`).

## TagUsageField(field)
The `usage_count` field `generic_tag_serializer()` uses - for a tag serializer of your own.

```python
from isik.django.apps.tags.drf import TagUsageField

usage_count = TagUsageField(Post.topics)
```
