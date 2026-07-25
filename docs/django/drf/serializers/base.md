# base

`BaseModelSerializer` composes every serializer mixin in this package onto `ModelSerializer` in one base class: `ModelSerializerRegistryMixin`, `CreateOnlyFieldsMixin`, `MetaCombiningMixin` (combining `Meta.relational_fields`), `RequestContextMixin`, `ConditionalSerializerMixin`.

```python
class WidgetSerializer(BaseModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        create_only_fields = ["name"]
        relational_fields = {"owner": relational_serializer(OwnerSerializer)}
```

- Pick and compose the individual mixins directly instead (see their own docs) if a project doesn't want the whole stack - `BaseModelSerializer` is just a convenience default, not a required entry point.
- `meta_fields_to_combine = ["relational_fields"]` and an empty `_Meta.relational_fields = {}` are set here specifically so `Meta.relational_fields` always merges across the hierarchy rather than needing every subclass to redeclare it.
