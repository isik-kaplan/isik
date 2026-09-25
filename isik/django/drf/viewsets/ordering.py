from django.core.exceptions import ImproperlyConfigured
from rest_framework.filters import OrderingFilter

from isik._internal.translation import gettext as _


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
                    _(
                        "%(viewset)s sets ordering_fields but OrderingFilter (or a subclass) is "
                        "not in filter_backends - add it to REST_FRAMEWORK['DEFAULT_FILTER_BACKENDS'] "
                        "or set filter_backends directly."
                    )
                    % {"viewset": cls.__name__}
                )
        if not cls.allow_reverse_ordering:
            return
        missing_reverses = [
            f"-{field}" for field in ordering_fields if not field.startswith("-") and f"-{field}" not in ordering_fields
        ]
        if missing_reverses:
            cls.ordering_fields = ordering_fields + missing_reverses


class DeclaredOrderingFilter(OrderingFilter):
    """
    `OrderingFilter` that also resolves `declared_ordering` - see `DeclaredOrderingMixin`. A
    drop-in replacement for plain `OrderingFilter`: with no `declared_ordering` set, it behaves
    identically.
    """

    def remove_invalid_fields(self, queryset, fields, view, request):
        declared_ordering = getattr(view, "declared_ordering", {})
        # `request` only reaches OrderingFilter.get_default_valid_fields() (the ordering_fields=None
        # fallback), as serializer context - which field *names* it returns doesn't depend on it, so
        # no test can observe this forwarding either way.
        valid_fields = set(super().remove_invalid_fields(queryset, fields, view, request))  # pragma: no mutate

        def term_valid(term):
            key = term[1:] if term.startswith("-") else term
            return term in valid_fields or key in declared_ordering

        return [term for term in fields if term_valid(term)]

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset
        declared_ordering = getattr(view, "declared_ordering", {})
        resolved = []
        for term in ordering:
            reverse = term.startswith("-")
            key = term[1:] if reverse else term
            target = declared_ordering.get(key)
            if target is None:
                resolved.append(term)
                continue
            targets = target if isinstance(target, (list, tuple)) else [target]
            resolved.extend(self._flip(field) if reverse and isinstance(field, str) else field for field in targets)
        return queryset.order_by(*resolved)

    @staticmethod
    def _flip(field):
        # Only a plain field name has a sign to flip - a `django.db.models.F(...).desc()`/`.asc()`
        # or other ordering expression already encodes its own direction, so it passes through as-is
        # regardless of the request's "-" prefix.
        return field[1:] if field.startswith("-") else f"-{field}"


class DeclaredOrderingMixin:
    """
    Adds `declared_ordering` - a dict mapping a `?ordering=` key exposed to API clients to the real
    field(s) (or an ordering expression) it maps to, for a value that isn't itself a real field:

        class WidgetViewSet(DeclaredOrderingMixin, ModelViewSet):
            declared_ordering = {"name": ("last_name", "first_name")}
            filter_backends = [DeclaredOrderingFilter]
            # ?ordering=name  -> order_by("last_name", "first_name")
            # ?ordering=-name -> order_by("-last_name", "-first_name")

    A key's "-" counterpart is always valid too and flips the sign of every field it maps to - even
    one that already carries its own sign, e.g. `{"newest": "-created_at"}` makes `?ordering=-newest`
    resolve to `created_at`. An expression target (e.g. `F("created_at").desc()`) passes through
    unchanged instead, since it already encodes its own direction. Declared keys don't need to be
    listed in `ordering_fields` too - they're valid on their own.

    `DeclaredOrderingFilter` (or a subclass) must be present in `filter_backends` for this to have
    any effect - plain `OrderingFilter` doesn't know how to resolve it - so this fails loudly at
    class-definition time instead of silently ordering by a literal, nonexistent field.
    """

    declared_ordering = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.declared_ordering:
            return
        filter_backends = getattr(cls, "filter_backends", [])
        if not any(issubclass(backend, DeclaredOrderingFilter) for backend in filter_backends):
            raise ImproperlyConfigured(
                _(
                    "%(viewset)s sets declared_ordering but DeclaredOrderingFilter (or a subclass) "
                    "is not in filter_backends - add it to REST_FRAMEWORK['DEFAULT_FILTER_BACKENDS'] or "
                    "set filter_backends directly."
                )
                % {"viewset": cls.__name__}
            )
