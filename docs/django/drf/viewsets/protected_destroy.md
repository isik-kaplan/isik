# protected_destroy

`ProtectedDestroyMixin` turns a `ProtectedError` on delete into a clean 400 response instead of a raw 500, reporting which models and which field blocked the delete - not the individual blocking objects, since there can be many and the caller can query for them directly.

```python
class WidgetViewSet(ProtectedDestroyMixin, ModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer

# DELETE /widgets/1/ where a Comment.widget PROTECTs the row
# -> 400 {"protected_by": [{"model": "Comment", "field": "widget"}]}
```

- Blockers are grouped by `(model, field)` pair and deduplicated/sorted - two `Comment` rows blocking the same delete produce one entry, not two.
- `field` comes back as `None` if no concrete relation on the blocking model actually resolves to the instance being deleted (an edge case rather than something real `ProtectedError`s from Django hit in practice).
