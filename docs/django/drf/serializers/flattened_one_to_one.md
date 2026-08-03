# flattened_one_to_one

`FlattenedOneToOneMixin` - exposes a reverse one-to-one relation's fields as if they were declared directly on the parent serializer, read and write-through.

```python
class WidgetProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = WidgetProfile
        fields = ["bio"]

class WidgetSerializer(FlattenedOneToOneMixin, BaseModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]
        flattened_one_to_one_fields = {"profile": WidgetProfileSerializer}

# GET -> {"id": ..., "name": ..., "bio": ...} - bio is None if no WidgetProfile row exists yet
# Writing bio creates the WidgetProfile row on create, updates it in place on update
```

- `flattened_one_to_one_fields = {accessor: NestedSerializerClass}` - `accessor` is the reverse one-to-one field name on the model (the `related_name` side). Validated at class-definition time: `accessor` must resolve to a real `OneToOneRel` on `Meta.model`, and two different accessors can't declare a field with the same name - both raise `ImproperlyConfigured` immediately rather than surfacing later.
- Read side needs no extra work: DRF's own dotted-`source` attribute lookup already returns `None` for the whole path when the related row doesn't exist, since Django's reverse-O2O-missing exception subclasses both `ObjectDoesNotExist` and `AttributeError`.
- Write side (`create()`/`update()`, both wrapped in their own `@transaction.atomic`) creates the related row if the parent doesn't have one yet, or updates it in place otherwise. The related write failing rolls back the parent write too - the two are one atomic operation even though the related model isn't touched by DRF's own `ModelSerializer.create()`/`update()`.
- Known non-goal: only per-field validation on the nested serializer carries over (its fields are harvested individually, not run through its own `is_valid()`) - `Meta.validators`/object-level `validate()` on the nested serializer class don't apply.
- Not combined into `MetaCombiningMixin`'s `meta_fields_to_combine` by default - `flattened_one_to_one_fields` is inherently per-model, unlike `relational_fields`. A project that wants it merged across a hierarchy for the same model can opt in itself; if it does, this mixin needs to sit to the left of `MetaCombiningMixin` in the bases list so its validation sees the already-merged dict.
