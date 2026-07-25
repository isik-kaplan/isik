# gfk

`AutoGenericForeignKey` is a `GenericForeignKey` subclass that creates its own companion
content-type and object-id fields via `contribute_to_class`, instead of requiring them declared by
hand alongside it.

```python
from isik.django.apps.common.fields.gfk import AutoGenericForeignKey

class Note(BaseModel):
    body = models.CharField(max_length=200)
    target = AutoGenericForeignKey(limit_models_to=[Widget])
```

- A field named `target` produces `target_content_type` (FK to `ContentType`,
  `on_delete=models.CASCADE` by default) and `target_object_id` (`UUIDField` by default, to match
  a UUID pk) — override with `object_id_field`/`object_id_field_kwargs` for a non-UUID pk, or
  `on_delete` for different deletion behavior.
- `limit_models_to` accepts model classes or `"app_label.Model"` strings and becomes
  `limit_choices_to` on the content-type FK; omitted, any content type is allowed.
