# schema_generation

`none_during_schema_generation` - decorator for a viewset method (typically `get_queryset()`) that returns `self.model.objects.none()` instead of running the wrapped method during drf-spectacular/drf-yasg schema generation.

```python
class OrganizationViewSet(BaseModelViewSet):
    @none_during_schema_generation
    def get_queryset(self):
        return super().get_queryset().filter(members__user=self.request.user)
```

- `self.request.user` is `AnonymousUser` while a schema is being generated, not a real user - a `get_queryset()` filtering by the current user would otherwise crash or return the wrong thing during that dry run. Checked via `getattr(self, "swagger_fake_view", False)`, the standard drf-yasg/drf-spectacular convention.
- A decorator, not a mixin, deliberately - apply it to just the one method that actually needs it, without touching a viewset's MRO.
- Assumes `self.model` is already set (e.g. via `RequiredAttributesMixin`, as `BaseModelViewSet` already requires).
