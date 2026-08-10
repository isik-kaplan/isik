from hypothesis import given
from hypothesis import strategies as st

from isik.common.utils.iterables import all_combinations, first_of, not_none, purge_iterable, purge_mapping


class TestNotNone:
    def test_none_is_false(self):
        assert not_none(None) is False

    @given(st.integers() | st.text() | st.booleans())
    def test_anything_else_is_true(self, value):
        assert not_none(value) is True


class TestFirstOf:
    def test_returns_first_truthy_value(self):
        assert first_of([None, None, "x"]) == "x"

    def test_returns_default_when_nothing_matches(self):
        assert first_of([None, None], default="fallback") == "fallback"

    def test_uses_a_custom_predicate(self):
        assert first_of([None, "a", "b"], pred=not_none) == "a"

    def test_the_predicate_is_actually_used_not_just_plain_truthiness(self):
        # filter(None, ...) (plain truthy filtering) would skip 0 as falsy and return 1 instead -
        # this pred looks for 0 specifically, which only a real pred= passthrough finds.
        assert first_of([1, 0, 2], pred=lambda x: x == 0) == 0

    @given(st.lists(st.none(), max_size=5))
    def test_all_falsy_values_return_default(self, values):
        assert first_of(values, default="fallback") == "fallback"


@given(st.lists(st.integers()), st.lists(st.integers()))
def test_purge_iterable_drops_only_the_given_items(iterable, items):
    result = purge_iterable(iterable, items)
    assert all(item not in items for item in result)
    assert all(item in iterable for item in result)


def test_purge_mapping_drops_only_the_given_keys():
    assert purge_mapping({"a": 1, "b": 2, "c": 3}, {"b"}) == {"a": 1, "c": 3}


@given(st.lists(st.integers(), unique=True, max_size=4))
def test_all_combinations_produces_every_non_empty_subset(options):
    combinations = all_combinations(options)
    expected_count = 2 ** len(options) - 1 if options else 0
    assert len(combinations) == expected_count
    assert all(len(combo) > 0 for combo in combinations)
    assert all(set(combo) <= set(options) for combo in combinations)
