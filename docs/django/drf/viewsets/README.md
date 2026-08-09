# viewsets

Viewset mixins composed together in `base.py`'s `BaseModelViewSet` - registry, per-action serializer selection, filtering/ordering wiring, and protected-delete handling.

- [base.md](base.md) - `BaseModelViewSet`, everything below composed onto `ModelViewSet`
- [registry.md](registry.md) - `ViewSetRegistryMixin`, model -> viewset lookup
- [action_serializer_class.md](action_serializer_class.md) - `serializer_class_action_map` per action
- [filterset.md](filterset.md) - builds `filterset_class` from `filterset_fields`/`declared_filters`
- [history.md](history.md) - `HistoryMixin`, a paginated/filterable `history/` action for a `@track_events()`-tracked model
- [ordering.md](ordering.md) - auto `-field` reverse ordering counterparts
- [protected_destroy.md](protected_destroy.md) - `ProtectedError` -> clean 400 instead of 500
- [schema_generation.md](schema_generation.md) - `none_during_schema_generation`, safe `get_queryset()` during schema generation
