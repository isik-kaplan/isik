import pytest
from django.core.exceptions import ValidationError
from hypothesis import given
from hypothesis import strategies as st

from isik.django.apps.common.skippable_validators import SkipFieldValidators, SkipNamedValidators, make_skippable
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


def test_field_validators_run_by_default():
    with pytest.raises(ValidationError):
        Widget.objects.create(name="bolt", count=-1)


def test_skip_field_validators_does_not_swallow_an_unrelated_exception_from_the_block():
    with pytest.raises(RuntimeError, match="boom"):
        with SkipFieldValidators("count"):
            raise RuntimeError("boom")


def test_skip_named_validators_does_not_swallow_an_unrelated_exception_from_the_block():
    with pytest.raises(RuntimeError, match="boom"):
        with SkipNamedValidators("positive_only"):
            raise RuntimeError("boom")


def test_skip_field_validators_bypasses_only_the_named_field():
    widget = Widget(name="bolt", count=-1)
    with widget.skip_field_validators("count"):
        widget.full_clean()


def test_skip_field_validators_does_not_affect_other_fields():
    widget = Widget(name="x" * 200, count=-1)
    with widget.skip_field_validators("count"):
        with pytest.raises(ValidationError):
            widget.full_clean()


def test_skip_named_validators_bypasses_by_validator_name():
    widget = Widget(name="bolt", count=-1)
    with widget.skip_named_validators("positive_only"):
        widget.full_clean()


def test_nested_skip_field_validators_adds_to_the_outer_scope_not_replaces_it():
    widget = Widget(name="x" * 200, count=-1)
    with widget.skip_field_validators("count"):
        with widget.skip_field_validators("name"):
            widget.full_clean()  # both count and name skipped here
        # back in the outer scope - count should still be skipped, name should not
        with pytest.raises(ValidationError):
            widget.full_clean()


def test_nested_skip_named_validators_adds_to_the_outer_scope_not_replaces_it():
    widget = Widget(name="bolt", count=-1)
    with widget.skip_named_validators("positive_only"):
        with widget.skip_named_validators("never_matches_anything"):
            widget.full_clean()  # positive_only is still skipped from the outer scope
        # still inside the outer scope after the inner one exits
        widget.full_clean()


def test_skip_is_scoped_to_the_with_block():
    widget = Widget(name="bolt", count=-1)
    with widget.skip_field_validators("count"):
        pass
    with pytest.raises(ValidationError):
        widget.full_clean()


def test_skip_field_validators_can_be_used_as_a_decorator():
    widget = Widget(name="bolt", count=-1)

    @widget.skip_field_validators("count")
    def clean():
        widget.full_clean()

    clean()


class TestMakeSkippable:
    def test_wrapped_validator_is_marked_skippable(self):
        def validator(value):
            raise ValidationError("nope")

        wrapped = make_skippable(validator, field_name="count")
        assert wrapped._is_skippable is True

    def test_field_validators_on_a_real_model_are_already_wrapped(self):
        field = Widget._meta.get_field("count")
        assert all(getattr(v, "_is_skippable", False) for v in field.validators)

    @given(st.integers(min_value=-1000, max_value=-1))
    def test_wrapped_validator_still_raises_when_nothing_is_skipped(self, value):
        widget = Widget(name="bolt", count=value)
        with pytest.raises(ValidationError):
            widget.full_clean()

    def test_without_an_explicit_name_the_validators_own_name_is_used_to_skip_it(self):
        def my_custom_validator(value):
            raise ValidationError("nope")

        wrapped = make_skippable(my_custom_validator, field_name="count")

        with SkipNamedValidators("my_custom_validator"):
            assert wrapped(-1) is None

    def test_explicit_name_is_used_instead_of_the_validators_own_name(self):
        def validator(value):
            raise ValidationError("nope")

        wrapped = make_skippable(validator, field_name="count", name="custom_name")

        with SkipNamedValidators("custom_name"):
            assert wrapped(-1) is None

    def test_the_validators_own_name_no_longer_skips_once_an_explicit_name_is_given(self):
        def validator(value):
            raise ValidationError("nope")

        wrapped = make_skippable(validator, field_name="count", name="custom_name")

        with SkipNamedValidators("validator"):
            with pytest.raises(ValidationError):
                wrapped(-1)
