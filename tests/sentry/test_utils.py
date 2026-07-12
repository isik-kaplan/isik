import pytest
import sentry_sdk

from isik.sentry.utils import suppress_callable_to_sentry, suppress_to_sentry


# Suppress/return-value behavior is already covered by SuppressAndRun/suppress_callable
# in tests/common/test_utils.py - what's specific here is the sentry wiring.


def test_suppress_to_sentry_is_wired_to_sentry_capture_exception():
    assert suppress_to_sentry.keywords["func"] is sentry_sdk.capture_exception


def test_suppress_to_sentry_suppresses_the_given_exception():
    with suppress_to_sentry(ValueError):
        raise ValueError("boom")


def test_suppress_to_sentry_does_not_suppress_other_exceptions():
    with pytest.raises(TypeError):
        with suppress_to_sentry(ValueError):
            raise TypeError("boom")


def test_suppress_callable_to_sentry_is_wired_to_sentry_capture_exception():
    assert suppress_callable_to_sentry.keywords["func"] is sentry_sdk.capture_exception


def test_suppress_callable_to_sentry_returns_the_static_value_on_suppression():
    @suppress_callable_to_sentry(ValueError, return_value="fallback")
    def parse(value):
        raise ValueError("boom")

    assert parse(1) == "fallback"


def test_suppress_callable_to_sentry_passes_through_on_success():
    @suppress_callable_to_sentry(ValueError, return_value="fallback")
    def parse(value):
        return value

    assert parse(5) == 5
