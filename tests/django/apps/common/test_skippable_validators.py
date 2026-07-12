import pytest
from django.core.exceptions import ValidationError
from hypothesis import given
from hypothesis import strategies as st

from isik.django.apps.common.skippable_validators import make_skippable
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


def test_field_validators_run_by_default():
    with pytest.raises(ValidationError):
        Widget.objects.create(name="bolt", count=-1)


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
