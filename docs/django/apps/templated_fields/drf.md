# drf

DRF glue for `templated_fields`: a serializer mixin that auto-renders `TemplateCharField`/`TemplateTextField` as `{"raw", "rendered"}`, and a viewset mixin that adds a live-preview action for an edit-in-progress raw value.

## TemplateFieldsModelSerializerMixin

Mix in before `ModelSerializer` (MRO must let it win `serializer_field_mapping`) to render every template field on the model as `{"raw": <source>, "rendered": <output>}` on read, while writes still take a plain string.

```python
class PostSerializer(TemplateFieldsModelSerializerMixin, BaseModelSerializer):
    class Meta:
        model = Post
        fields = ["id", "default_text"]

# reads as: {"default_text": {"raw": "hi {{ title }}", "rendered": "hi hello"}}
# writes with a plain string: {"default_text": "bye {{ title }}"}
```

- Write-side validation runs the template through the model field's own policy via `TemplateFieldSerializer.bind()`, so a policy-violating or syntactically broken template comes back as a normal 400 in `serializer.errors` instead of only failing at `.save()`.
- A render-time error (e.g. `available()`'s context shrank since the value was saved) degrades `"rendered"` to `None` instead of raising - listing rows shouldn't 500 because one row's template broke.

## TemplateFieldPreviewMixin

Adds `POST /<pk>/preview-template/` to a `ModelViewSet` - renders a raw value against the existing instance without saving, for a live preview while a user is still typing.

```python
class PostViewSet(TemplateFieldPreviewMixin, ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer

# POST /api/posts/1/preview-template/ {"field": "default_text", "raw": "hi {{ title }}"}
# -> 200 {"rendered": "hi hello"}
# -> 400 {"raw": [...]} or {"field": [...]} on an invalid template/field name
```
