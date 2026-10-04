from django_filters.constants import EMPTY_VALUES


def make_filters(field, filter_cls, lookup_expressions):
    """
    Builds a dict of django-filter filters for a single field across several lookup
    expressions, suitable for assigning to a FilterSet's declarative filters.

    Example:
        class MyFilterSet(FilterSet):
            locals().update(make_filters("created_at", DateTimeFilter, ["exact", "gte", "lte"]))
            # produces: created_at, created_at__gte, created_at__lte
    """
    get_key = lambda expr: f"{field}__{expr}" if expr != "exact" else field  # NOQA
    return {get_key(expr): filter_cls(field_name=field, lookup_expr=expr) for expr in lookup_expressions}


class NarrowingFilterMixin:
    """
    A django-filter filter that leaves the queryset alone when its value wasn't given, and narrows it
    with `narrow(qs, value)` when it was - mix in before the filter class:

        class PublishedAfterFilter(NarrowingFilterMixin, DateFilter):
            def narrow(self, qs, value):
                return qs.filter(published_at__date__gt=value)

    django-filter's own `Filter.filter()` skips an empty value, but overriding `filter()` loses that,
    so every custom filter writes `if value in EMPTY_VALUES: return qs` again. This writes it once.
    """

    def filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs
        return self.narrow(qs, value)

    def narrow(self, qs, value):
        """The queryset narrowed by `value` - called only with a value that was given."""
        raise NotImplementedError
