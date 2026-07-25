# drf

`generic_note_serializer(model)` builds a default `ModelSerializer` for a generated `<Host>Note` model - `id`, `body`, `created_at`, `updated_at`, read-only `user`. `body` is writable, meant to back a `ModelViewSet` scoped to the requesting user's own rows.

```python
from isik.django.apps.feedback.notes.drf import generic_note_serializer

NoteSerializer = generic_note_serializer(Post.notes.model)
```

- `isik.django.drf.permissions.is_owner` covers the object-level actions (retrieve/update/destroy); list/create still need the view's own `get_queryset()` filter and `perform_create()` to set `user`.
