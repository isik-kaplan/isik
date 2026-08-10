import pytest
from hypothesis import given
from hypothesis import strategies as st

from isik.common.utils.functional import (
    cloned,
    enabled_if,
    identity,
    noop,
    raises,
    require_exclusive_keys,
    returns,
    with_attrs,
)


def test_noop_returns_none_regardless_of_arguments():
    assert noop() is None
    assert noop(1, 2, key="value") is None


@given(st.integers() | st.text() | st.none())
def test_identity_returns_its_input_unchanged(value):
    assert identity(value) is value


def test_with_attrs_sets_attributes_on_the_function():
    @with_attrs(tag="foo", weight=3)
    def func():
        pass

    assert func.tag == "foo"
    assert func.weight == 3


def test_with_attrs_returns_the_same_function_object():
    def func():
        pass

    assert with_attrs(tag="foo")(func) is func


@given(st.integers() | st.text())
def test_returns_ignores_all_arguments(value):
    always = returns(value)
    assert always() == value
    assert always(1, 2, key="value") == value


def test_raises_creates_a_callable_that_raises_on_call():
    thrower = raises(ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        thrower()


class TestRequireExclusiveKeys:
    def test_raises_when_no_conditions_given(self):
        with pytest.raises(ValueError, match="^At least one condition dict must be provided\\.$"):
            require_exclusive_keys()

    def test_matching_a_single_condition_calls_the_function(self):
        @require_exclusive_keys({"by_url": ["url"]}, {"by_host": ["host", "port"]})
        def connect(url=None, host=None, port=None):
            return url or f"{host}:{port}"

        assert connect(url="https://example.com") == "https://example.com"
        assert connect(host="localhost", port=8080) == "localhost:8080"

    def test_matching_no_condition_raises_by_default(self):
        @require_exclusive_keys({"by_url": ["url"]}, {"by_host": ["host", "port"]})
        def connect(url=None, host=None, port=None):
            return url

        with pytest.raises(ValueError):
            connect()

    def test_matching_both_conditions_raises(self):
        @require_exclusive_keys({"by_url": ["url"]}, {"by_host": ["host", "port"]})
        def connect(url=None, host=None, port=None):
            return url

        with pytest.raises(ValueError):
            connect(url="https://example.com", host="localhost", port=8080)

    def test_allow_empty_permits_no_governed_keys(self):
        @require_exclusive_keys({"by_value": ["value"]}, allow_empty=True)
        def build(value=None):
            return value

        assert build() is None

    def test_ungoverned_keys_are_ignored(self):
        @require_exclusive_keys({"by_url": ["url"]})
        def connect(url=None, db=None):
            return url, db

        assert connect(url="u", db=1) == ("u", 1)


def test_cloned_produces_an_independent_copy():
    def original(x):
        return x

    foo = with_attrs(tag="foo")(cloned(original))
    bar = with_attrs(tag="bar")(cloned(original))

    assert foo.tag == "foo"
    assert bar.tag == "bar"
    assert not hasattr(original, "tag")
    assert foo(1) == 1
    assert bar(1) == 1


class TestEnabledIf:
    def test_true_condition_leaves_function_unchanged_in_behavior(self):
        @enabled_if(True, if_not_enabled_return_value=None)
        def func():
            return "real"

        assert func() == "real"

    def test_false_condition_replaces_the_return_value(self):
        @enabled_if(False, if_not_enabled_return_value="disabled")
        def func():
            return "real"

        assert func() == "disabled"

    def test_condition_can_be_a_callable(self):
        @enabled_if(lambda: False, if_not_enabled_return_value="disabled")
        def func():
            return "real"

        assert func() == "disabled"

    def test_does_not_mutate_the_original_function(self):
        def original():
            return "real"

        enabled_if(False, if_not_enabled_return_value="disabled")(cloned(original))
        assert original() == "real"
