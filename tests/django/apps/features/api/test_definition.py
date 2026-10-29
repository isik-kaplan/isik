"""What the features views are made of, built as a project's import builds them: serializers, exemptions, gates."""

import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.validators import MinValueValidator
from django.db import models
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from isik.django.apps.features import declarations, drf
from isik.django.apps.features.declarations import Setting
from isik.django.apps.features.operations import grant
from isik.django.apps.idempotency.drf import NoIdempotencyKey
from isik.django.drf.viewsets.preconditions import NoPrecondition


def shape(serializer_class):
    """{field: (its class, whether it allows null, its help text)}."""
    return {
        name: (type(field).__name__, field.allow_null, str(field.help_text) if field.help_text else None)
        for name, field in serializer_class().fields.items()
    }


COMMON = {
    "name": ("CharField", False, "The setting's name - the key it's changed by."),
    "type": ("CharField", False, "What kind of value it holds."),
    "label": ("CharField", True, "Its name, as a person reads it."),
    "description": ("CharField", True, "What it does."),
    "origin": (
        "ChoiceField",
        False,
        "Why it's this value: forced, for want of a feature, limited, yours, or the default.",
    ),
    "forced_reason": ("CharField", True, "Why it was forced, if you may read it."),
    "may_change": ("BooleanField", False, "Whether you may change it."),
    "requires_feature": ("CharField", True, "The feature it needs, where you may see it - else none."),
}


class TestTheOwnExemptions:
    def test_each_is_isiks_own_and_says_why(self):
        made = drf._own_exemptions()

        assert set(made) == {"setting", "setting_key"}
        assert isinstance(made["setting"], NoPrecondition)
        assert made["setting"] == (
            "compares If-Match itself, with the holder's settings version - a row that may not exist yet"
        )
        assert isinstance(made["setting_key"], NoIdempotencyKey)
        assert made["setting_key"] == "sets the holder's own value to the one named; a retry changes nothing"


class TestTheSettingSerializers:
    def test_a_choice(self):
        made = drf._setting_serializer("choice")

        assert (made.__name__, made.__doc__) == ("ChoiceSetting", "A setting holding a choice value.")
        assert shape(made) == {
            **COMMON,
            "value": ("JSONField", False, None),
            "default": ("JSONField", False, None),
            "chosen": ("JSONField", True, "Your own choice, or none yet."),
            "choices": ("ListSerializer", False, "What it may be."),
        }
        assert made().fields["choices"].many is True
        assert list(made().fields["origin"].choices) == ["forced", "feature_missing", "limited", "own", "default"]

    @pytest.mark.parametrize(
        ("kind", "field"), [("int", "IntegerField"), ("decimal", "DecimalField"), ("float", "FloatField")]
    )
    def test_a_number_has_limits(self, kind, field):
        assert shape(drf._setting_serializer(kind)) == {
            **COMMON,
            **dict.fromkeys(("value", "default"), (field, False, None)),
            "chosen": (field, True, "Your own choice, or none yet."),
            **dict.fromkeys(("minimum", "maximum"), (field, True, None)),
        }

    @pytest.mark.parametrize(
        ("kind", "field"), [("bool", "BooleanField"), ("duration", "DurationField"), ("text", "CharField")]
    )
    def test_the_rest_only_their_value(self, kind, field):
        assert shape(drf._setting_serializer(kind)) == {
            **COMMON,
            **dict.fromkeys(("value", "default"), (field, False, None)),
            "chosen": (field, True, "Your own choice, or none yet."),
        }

    def test_what_a_patch_names(self):
        made = drf._value_serializer("int")

        assert made.__name__ == "IntSettingValue"
        assert shape(made) == {
            "type": ("CharField", False, "The setting's type, as listed."),
            "value": ("IntegerField", False, None),
        }

    def test_the_text_types_take_a_blank(self):
        assert drf.VALUE_FIELDS["text"]().allow_blank is True
        assert isinstance(drf.VALUE_FIELDS["choice"](), serializers.JSONField)


class TestLimits:
    def test_from_the_fields_validators(self):
        assert drf._limits(models.FloatField(validators=[MinValueValidator(2)])) == (2, None)
        assert drf._limits(models.FloatField()) == (None, None)


class TestTheDefaultsVersion:
    def test_a_strong_tag_moving_with_the_defaults(self, monkeypatch):
        before = drf.defaults_etag()
        theme = declarations.settings["theme"]
        monkeypatch.setitem(declarations.settings, "theme", Setting(**{**theme.__dict__, "default": "dark"}))

        assert re.fullmatch(r'"defaults-[0-9a-f]{16}"', before)
        assert drf.defaults_etag() != before


@pytest.mark.django_db
class TestHasFeature:
    def request_from(self, user):
        request = APIRequestFactory().get("/")
        request.user = user
        return request

    def test_what_it_is(self, monkeypatch):
        monkeypatch.setattr(declarations, "checked", set())

        made = drf.HasFeature("beta")

        assert (made.__name__, made.__qualname__) == ("HasFeature_beta", "HasFeature_beta")
        assert (str(made.message), made.code) == ("This needs a feature you don't have.", "feature_missing")
        assert declarations.checked == {"beta"}

    def test_it_needs_a_signed_in_holder_with_the_feature(self, alice, bob, settings):
        gate = drf.HasFeature("beta")()

        assert gate.has_permission(self.request_from(AnonymousUser()), None) is False
        assert gate.has_permission(self.request_from(alice), None) is False
        grant(alice, "beta", source="manual")
        assert gate.has_permission(self.request_from(alice), None) is True

        settings.ISIK_FEATURES = {
            **settings.ISIK_FEATURES,
            "HOLDER": lambda request: bob if request.user == alice else None,
        }
        assert gate.has_permission(self.request_from(alice), None) is False


@pytest.mark.django_db
def test_a_setting_locked_for_want_of_its_feature_isnt_the_holders_to_change(alice):
    viewset = drf.SettingViewSet()
    viewset.request = APIRequestFactory().patch("/")
    viewset.request.user = alice

    assert (viewset.may_change(alice, "theme", "feature_missing"), viewset.may_change(alice, "theme", "own")) == (
        False,
        True,
    )
