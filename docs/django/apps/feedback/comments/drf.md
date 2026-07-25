# drf

`generic_comment_serializer(model)` builds a default `ModelSerializer` for a generated `<Host>Comment` model - `id`, `body`, `created_at`, `updated_at`, read-only `user`. `body`'s serializer field is derived from whatever the model field actually is (`TextField`/`CharField` for plain text, `JSONField` for `comments(tiptap=True)`), so model-level validators - including `TiptapValidator` - carry over automatically.

```python
from isik.django.apps.feedback.comments.drf import generic_comment_serializer

CommentSerializer = generic_comment_serializer(Post.comments.model)
```
