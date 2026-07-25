# drf

`generic_bookmark_serializer(model)` builds a default `ModelSerializer` for a generated `<Host>Bookmark` model - `id`, `created_at`, read-only `user`. Use as-is or subclass further.

```python
from isik.django.apps.feedback.bookmarks.drf import generic_bookmark_serializer

BookmarkSerializer = generic_bookmark_serializer(Post.bookmarks.model)
```

- Every field is read-only, so a viewset built on it only needs to support retrieve/destroy, not update - see `test_composes_with_is_owner_for_a_private_bookmark_viewset`.
