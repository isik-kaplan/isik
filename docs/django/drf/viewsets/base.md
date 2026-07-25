# base

`BaseModelViewSet` composes every viewset mixin in this package onto `ModelViewSet`: `RequiredAttributesMixin` (`model`/`endpoint`/`serializer_class` required), `ViewSetRegistryMixin`, `ActionSerializerClassMixin`, `ProtectedDestroyMixin`, `ReverseOrderingMixin`, `FilterSetMixin`.

```python
class WidgetViewSet(BaseModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
    filterset_fields = ["name"]
    ordering_fields = ["created_at"]

# missing `endpoint` raises TypeError at class-definition time, before ViewSetRegistryMixin
# would otherwise register the (broken) class
```

- `model` is required explicitly and is the source of truth for `get_queryset()` - not `serializer_class.Meta.model` - matching the "required explicit" choice made for `endpoint` too.
- Pick and compose the individual mixins directly instead (see their own docs) if a project doesn't want the whole stack.
