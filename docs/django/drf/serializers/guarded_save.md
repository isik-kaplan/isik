# guarded_save

`FieldGuardsOnSaveMixin` runs the current viewset's [field guards](../permissions.md#guarding) inside `save()`, before anything reaches the database. It's part of `BaseModelSerializer`.

A write is checked however its serializer was built: through `get_serializer()`, or constructed by hand in an overridden handler or a custom `@action`.

```python
@action(detail=True, methods=["post"])
def recount(self, request, pk=None):
    body = WidgetSerializer(self.get_object(), data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    body.save()  # the viewset's field guards run here, and refuse before the write
    return Response(body.data)
```

- Values passed to `save(**kwargs)` count as part of the write, since DRF merges them over `validated_data`. `serializer.save(count=99)` is a change to `count` like any other.
- Only a serializer for the viewset's own model is checked (a proxy counts as the same model). Another model's serializer saved along the way, like an audit log, isn't held to guards written for rows it doesn't touch, and saving it doesn't count as having run the guards.
- It does nothing outside a [`GuardedFieldsMixin`](../viewsets/guarded_fields.md) viewset's write request, such as a shell, a task, or a test saving a serializer directly.
