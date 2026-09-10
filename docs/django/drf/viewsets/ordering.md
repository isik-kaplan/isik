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

## `DeclaredOrderingMixin`

`declared_ordering` maps a `?ordering=` key exposed to API clients to the real field(s) - or ordering expression - it orders by, for a value that isn't itself a field (a stand-in for several columns, or one that shouldn't leak the real column name):

```python
class WidgetViewSet(DeclaredOrderingMixin, ModelViewSet):
    declared_ordering = {"name": ("last_name", "first_name")}
    filter_backends = [DeclaredOrderingFilter]
    # ?ordering=name  -> order_by("last_name", "first_name")
    # ?ordering=-name -> order_by("-last_name", "-first_name")
```

- A key's `-` counterpart is always valid too, and flips the sign of every field it maps to - including a field that already carries its own sign, e.g. `{"newest": "-created_at"}` makes `?ordering=-newest` resolve to `created_at`.
- A target can be an ordering expression (e.g. `F("created_at").desc(nulls_last=True)`) instead of a field name - it's passed through as-is, the request's `-` prefix ignored, since the expression already encodes its own direction.
- Declared keys don't need to be listed in `ordering_fields` too - they're valid independently of it.
- `DeclaredOrderingFilter` (or a subclass) must be present in `filter_backends` in place of plain `OrderingFilter` - otherwise `declared_ordering` is silently ignored (or worse, ordered against a literal, nonexistent field) - so this raises `ImproperlyConfigured` at class-definition time instead. With no `declared_ordering` set, `DeclaredOrderingFilter` behaves identically to `OrderingFilter`, so it's a safe drop-in replacement.
