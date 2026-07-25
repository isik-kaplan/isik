# drf

DRF (Django REST Framework) helpers: error translation, filters, pagination, permissions, ad hoc schema builders, plus serializer/viewset mixin subfolders.

- [error_handling.md](error_handling.md) - `django_to_drf_validation_error` decorator
- [filters.md](filters.md) - `make_filters`, a lookup-expression dict builder
- [pagination.md](pagination.md) - `PageNumberPagination` with `total_pages`/`page_size`
- [permissions.md](permissions.md) - `ReadOnly`, `is_owner`, `user_property`, and other permission factories
- [schema.md](schema.md) - `FakeSerializer`/`FakeErrorSerializer` for ad hoc response shapes
- [serializers/](serializers/README.md) - model↔serializer registry, conditional include/only/exclude, create-only fields, Meta-combining, request context helpers
- [utils/](utils/README.md) - current-user defaults, lazy relation fields, related-count field
- [viewsets/](viewsets/README.md) - per-action serializer class, filterset/ordering wiring, protected-destroy handling, model↔viewset registry
