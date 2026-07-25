# ordering

`ReverseOrderingMixin` auto-adds a `"-field"` reverse-ordering counterpart for every entry in `ordering_fields`, so you list each field once and `?ordering=-created_at` still works.

```python
class WidgetViewSet(ReverseOrderingMixin, ModelViewSet):
    ordering_fields = ["created_at", "name"]
    filter_backends = [OrderingFilter]
    # WidgetViewSet.ordering_fields == ["created_at", "name", "-created_at", "-name"]
```

- Idempotent across subclassing levels - a field that already has its reverse counterpart (inherited or otherwise) isn't re-added. Set `allow_reverse_ordering = False` to opt out.
- If `ordering_fields` is set, `OrderingFilter` (or a subclass) must be present in `filter_backends` - otherwise DRF silently ignores it - so this raises `ImproperlyConfigured` at class-definition time instead.
