from isik.django.drf.filters import make_filters


class FakeFilter:
    """Stands in for a django-filter Filter subclass without adding a dependency on it."""

    def __init__(self, field_name, lookup_expr):
        self.field_name = field_name
        self.lookup_expr = lookup_expr


def test_exact_lookup_uses_the_bare_field_name():
    result = make_filters("created_at", FakeFilter, ["exact"])
    assert set(result) == {"created_at"}
    assert result["created_at"].lookup_expr == "exact"
    assert result["created_at"].field_name == "created_at"


def test_non_exact_lookups_are_suffixed_with_the_expression():
    result = make_filters("created_at", FakeFilter, ["exact", "gte", "lte"])
    assert set(result) == {"created_at", "created_at__gte", "created_at__lte"}
    assert result["created_at__gte"].lookup_expr == "gte"
    assert result["created_at__gte"].field_name == "created_at"


def test_no_lookup_expressions_produces_an_empty_dict():
    assert make_filters("name", FakeFilter, []) == {}
