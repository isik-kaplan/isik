from django.core.exceptions import ImproperlyConfigured
from rest_framework.filters import OrderingFilter


class ReverseOrderingMixin:
    """
    Auto-adds a "-field" reverse-ordering counterpart for every entry in `ordering_fields`, so you
    only have to list each field once and `?ordering=-created_at` still works. Set
    `allow_reverse_ordering = False` to opt out. Idempotent across multiple levels of subclassing -
    a field that already has its reverse counterpart present (inherited or otherwise) isn't
    re-added.

    If `ordering_fields` is set, `OrderingFilter` (or a subclass) must be present in
    `filter_backends` - otherwise DRF silently ignores `ordering_fields` and it has no effect - so
    this fails loudly at class-definition time instead.
    """

    allow_reverse_ordering = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        ordering_fields = getattr(cls, "ordering_fields", [])
        if ordering_fields:
            filter_backends = getattr(cls, "filter_backends", [])
            if not any(issubclass(backend, OrderingFilter) for backend in filter_backends):
                raise ImproperlyConfigured(
                    f"{cls.__name__} sets ordering_fields but OrderingFilter (or a subclass) is "
                    "not in filter_backends - add it to REST_FRAMEWORK['DEFAULT_FILTER_BACKENDS'] "
                    "or set filter_backends directly."
                )
        if not cls.allow_reverse_ordering:
            return
        missing_reverses = [
            f"-{field}" for field in ordering_fields if not field.startswith("-") and f"-{field}" not in ordering_fields
        ]
        if missing_reverses:
            cls.ordering_fields = ordering_fields + missing_reverses
