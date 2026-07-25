# meta_combining

`MetaCombiningMixin` merges specific `Meta` attributes with the values declared on `_Meta` (typically set once on a shared base class), instead of a subclass's own `Meta` clobbering them outright. A list attribute is concatenated (base first), a dict is merged (subclass wins on key conflicts).

```python
class BaseModelSerializer(MetaCombiningMixin, ModelSerializer):
    meta_fields_to_combine = ["relational_fields"]

    class _Meta:
        relational_fields = {"owner": relational_serializer(OwnerSerializer)}

class WidgetSerializer(BaseModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "owner", "tags"]
        relational_fields = {"tags": relational_serializer(TagSerializer)}
    # WidgetSerializer.Meta.relational_fields == {"owner": ..., "tags": ...}
```

- Exists because a normal `class Meta` on a subclass fully replaces the parent's `Meta` - there's no built-in way to say "add to what the base declared" for things like `relational_fields` without this. `_Meta` is a plain class attribute so it's inherited the ordinary way; combining reads `cls.__dict__.get("Meta")` (not `getattr`) so a subclass that doesn't declare its own `Meta` is left alone rather than mutating the already-combined parent `Meta` in place.
