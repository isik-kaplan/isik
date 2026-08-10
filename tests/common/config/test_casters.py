import pytest
from hypothesis import given
from hypothesis import strategies as st

from isik.common.config.casters import (
    boolean,
    caster,
    comma_separated_float_list,
    comma_separated_int_list,
    comma_separated_list,
    integer,
    string,
)


def test_caster_wraps_a_plain_function():
    @caster
    def shout(value):
        return value.upper()

    assert shout()("hi") == "HI"


def test_caster_sets_missing_default_only_when_given():
    @caster
    def shout(value):
        return value.upper()

    assert not hasattr(shout(), "missing_default")
    assert shout(missing_default="fallback").missing_default == "fallback"


def test_caster_distinguishes_an_explicit_none_default_from_not_given_at_all():
    # The internal "not given" sentinel has to be something no caller could ever pass for real -
    # if it were None, missing_default=None (a deliberate "fall back to None") would be
    # indistinguishable from omitting missing_default entirely.
    @caster
    def shout(value):
        return value.upper()

    assert shout(missing_default=None).missing_default is None


def test_caster_sets_error_default_only_when_given():
    @caster
    def shout(value):
        return value.upper()

    assert not hasattr(shout(), "error_default")
    assert shout(error_default="fallback").error_default == "fallback"


def test_string_and_integer_cast_directly():
    assert string()("hello") == "hello"
    assert integer()("42") == 42


class TestBoolean:
    @pytest.mark.parametrize("value", ["true", "True", "1"])
    def test_truthy_values(self, value):
        assert boolean()(value) is True

    @pytest.mark.parametrize("value", ["false", "False", "0"])
    def test_falsy_values(self, value):
        assert boolean()(value) is False

    def test_unparseable_value_raises(self):
        with pytest.raises(ValueError, match="can not be parsed"):
            boolean()("maybe")


def test_comma_separated_list_splits_on_comma():
    assert comma_separated_list()("a,b,c") == ["a", "b", "c"]


def test_comma_separated_int_list_casts_each_item():
    assert comma_separated_int_list()("1,2,3") == [1, 2, 3]


def test_comma_separated_float_list_casts_each_item():
    assert comma_separated_float_list()("1.5,2.5") == [1.5, 2.5]


@given(st.lists(st.integers(min_value=-1000, max_value=1000), min_size=1, max_size=10))
def test_comma_separated_int_list_round_trips(values):
    raw = ",".join(str(v) for v in values)
    assert comma_separated_int_list()(raw) == values
