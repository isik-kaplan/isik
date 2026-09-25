import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.common.skippable_validators import SkippableValidatorsMixin, make_skippable


pytestmark = pytest.mark.django_db


def _custom_validator(value):
    if value < 0:
        raise ValidationError("bad")


@isolate_apps("tests.testapp")
def test_wrap_field_validators_wraps_both_local_fields_and_m2m_fields():
    # A fresh isolate_apps model, not Widget - Widget is module-level and its class_prepared
    # signal only ever fires once at import time, so it would never observe a broken
    # _wrap_field_validators() under a tool that reruns pytest in the same process (e.g.
    # mutation testing).
    class FreshWidget(SkippableValidatorsMixin, models.Model):
        count = models.IntegerField(validators=[_custom_validator])
        tags = models.ManyToManyField("self", validators=[_custom_validator])

        class Meta:
            app_label = "testapp"

    count_field = FreshWidget._meta.get_field("count")
    assert all(v._is_skippable for v in count_field.validators)

    m2m_field = FreshWidget._meta.get_field("tags")
    assert all(v._is_skippable for v in m2m_field.validators)


@isolate_apps("tests.testapp")
def test_wrap_field_validators_does_not_double_wrap_an_already_skippable_validator():
    already_wrapped = make_skippable(_custom_validator, field_name="count")

    class AlreadyWrappedWidget(SkippableValidatorsMixin, models.Model):
        count = models.IntegerField(validators=[already_wrapped])

        class Meta:
            app_label = "testapp"

    field = AlreadyWrappedWidget._meta.get_field("count")
    assert already_wrapped in field.validators
    assert field.validators.count(already_wrapped) == 1


@isolate_apps("tests.testapp")
def test_wrapped_validator_is_named_after_its_field():
    class FreshWidget(SkippableValidatorsMixin, models.Model):
        count = models.IntegerField(validators=[_custom_validator])

        class Meta:
            app_label = "testapp"

    widget = FreshWidget(count=-1)
    with widget.skip_field_validators("count"):
        widget.full_clean(exclude=["id"])


@isolate_apps("tests.testapp")
def test_the_wrapped_validator_still_calls_through_to_the_real_original_one():
    # Without skip_field_validators engaged, the wrapped validator has to actually run the real
    # one it wraps (make_skippable(v, ...), not make_skippable(None, ...)) - not just be present.
    class FreshWidget(SkippableValidatorsMixin, models.Model):
        count = models.IntegerField(validators=[_custom_validator])

        class Meta:
            app_label = "testapp"

    widget = FreshWidget(count=-1)
    with pytest.raises(ValidationError):
        widget.full_clean(exclude=["id"])


@isolate_apps("tests.testapp")
@isolate_apps("tests.testapp")
def test_a_plain_model_prepared_after_a_skippable_one_is_left_alone():
    # The receiver runs for every model, so it has to check what it was handed - a plain model
    # that never mixes in SkippableValidatorsMixin must never have its validators wrapped.
    class PlainWidget(models.Model):
        count = models.IntegerField(validators=[_custom_validator])

        class Meta:
            app_label = "testapp"

    class FreshWidget(SkippableValidatorsMixin, models.Model):
        count = models.IntegerField(validators=[_custom_validator])

        class Meta:
            app_label = "testapp"

    plain_field = PlainWidget._meta.get_field("count")
    assert not any(getattr(v, "_is_skippable", False) for v in plain_field.validators)


def test_the_receiver_is_not_handed_a_model_that_does_not_mix_it_in():
    from isik.django.apps.common.skippable_validators.mixin import _wrap_field_validators

    field = type("Field", (), {"validators": [_custom_validator], "name": "count"})()
    meta = type("Meta", (), {"local_fields": [field], "local_many_to_many": []})()
    plain = type("Plain", (), {"_meta": meta})
    _wrap_field_validators(sender=plain)
    assert field.validators == [_custom_validator]


@isolate_apps("tests.testapp")
def test_defining_skippable_models_connects_no_receiver_per_class():
    # A per-class receiver is keyed by id(cls) and outlives the class, so it would fire for any later
    # class allocated at the same address - and pile up one per model.
    from django.db.models.signals import class_prepared

    before = len(class_prepared.receivers)
    for index in range(3):
        type(
            f"Counted{index}",
            (SkippableValidatorsMixin, models.Model),
            {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
        )
    assert len(class_prepared.receivers) == before
