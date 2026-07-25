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
Standalone `ModelSerializer` for the Tag model behind `field` - `id`, `name`, and a read-only `usage_count` (how many hosts currently carry this tag). Meant for a browse/autocomplete `ModelViewSet`, not for editing a host's own tags inline - see `generic_tag_field()` for that.

```python
from isik.django.apps.tags.drf import generic_tag_serializer

TagSerializer = generic_tag_serializer(Post.topics)
```
