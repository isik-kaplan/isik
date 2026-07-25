# related_count

`ModelRelatedCountField` - a read-only field reporting the count of a related manager, without pulling the related objects themselves into the response.

```python
comment_count = ModelRelatedCountField(related_name="comments")
# -> WidgetSerializer(widget).data["comment_count"] == widget.comments.count()
```

- Forces `source="*"` and `read_only=True` internally - `to_representation` gets the whole instance and calls `getattr(instance, related_name).count()` itself, rather than DRF resolving `source` to an attribute first.
