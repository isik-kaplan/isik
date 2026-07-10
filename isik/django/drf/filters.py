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
