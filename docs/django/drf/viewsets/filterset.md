# filterset

`FilterSetMixin` builds `filterset_class` on the fly from `filterset_fields` + `declared_filters`, instead of hand-writing a `FilterSet` subclass for every viewset.

```python
class WidgetViewSet(FilterSetMixin, ModelViewSet):
    model = Widget
    filterset_fields = ["name", "owner"]           # or {"name": ["exact", "icontains"]}
    declared_filters = {"name": CharFilter(lookup_expr="icontains")}
    filter_backends = [DjangoFilterBackend]
```

- `filterset_class` is a `classproperty` built fresh from `cls.model`/`filterset_fields`/`declared_filters` on every access - not cached on the class.
- If either `filterset_fields` or `declared_filters` is set, `DjangoFilterBackend` (or a subclass) must be present in `filter_backends`, or DRF silently ignores `filterset_class` entirely - this raises `ImproperlyConfigured` at class-definition time instead of failing silently at request time.
- `filterset_base` (default `FilterSet`) lets a project swap in its own base whose `Meta` gets inherited by the generated `Meta`, rather than starting from a blank slate.
