# registry

`ModelSerializerRegistryMixin` maintains a model -> serializer-class registry, populated automatically as subclasses are defined - so any serializer can be looked up later by the model it serializes (e.g. to build a cross-link, or resolve a polymorphic relation).

```python
class WidgetSerializer(ModelSerializerRegistryMixin, ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]

ModelSerializerRegistryMixin.get_for_model(Widget) is WidgetSerializer
WidgetSerializer.model_serializer_map(Widget, Tag, ignore_missing=True)  # {model: serializer_cls, ...}
```

- Registering two serializers for the same model raises `ImproperlyConfigured` immediately - almost always a mistake, so it fails fast rather than letting the second one silently win. Set `exempt_from_registry = True` on a subclass that shouldn't register at all (a schema-only serializer, or an intentional second serializer for an already-registered model).
- Set `is_base_class = True` on a project-level intermediate to give that branch its own private registry instead of sharing this one - several independent `BaseModelSerializer` hierarchies (e.g. one per API) can then each register the same model without colliding.
