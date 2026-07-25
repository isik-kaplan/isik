# action_serializer_class

`ActionSerializerClassMixin` picks a serializer class per action from `serializer_class_action_map`, instead of overriding `get_serializer_class()` by hand on every viewset that needs a different shape for one action.

```python
class WidgetViewSet(ActionSerializerClassMixin, ModelViewSet):
    serializer_class = WidgetSerializer
    serializer_class_action_map = {"list": WidgetListSerializer}

# view.action == "list"     -> WidgetListSerializer
# view.action == "retrieve" -> WidgetSerializer (falls back to serializer_class)
```
