"""
The opt-outs isik insists on a reason for - a registry exemption, a field left unguarded, an action
writing no guarded field - and the one rule they share: any non-blank string, never a bare `True`.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers, viewsets

from isik._internal.reasons import is_reason, require_reason, require_reasons
from isik.common.utils.declared_str import DeclaredStr, text
from isik.django.drf.serializers.registry import ModelSerializerRegistryMixin
from isik.django.drf.viewsets import GuardedFieldsMixin, ViewSetRegistryMixin
from tests.testapp.models import Widget


class Exemption(DeclaredStr):
    reason = text(min_length=20)


class TestTheRule:
    @pytest.mark.parametrize("value", ["why", " a reason ", Exemption("a project's own stricter reason")])
    def test_any_non_blank_string_is_a_reason(self, value):
        assert is_reason(value) is True

    @pytest.mark.parametrize("value", ["", "  \n", True, 1, None, ["why"]])
    def test_nothing_else_is(self, value):
        assert is_reason(value) is False

    @pytest.mark.parametrize("value", [False, None, "", ()])
    def test_not_opting_out_needs_no_reason(self, value):
        require_reason("Owner", "flag", value)

    @pytest.mark.parametrize("value", [True, 1, ["why"]])
    def test_opting_out_without_saying_why_is_refused(self, value):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_reason("Owner", "flag", value)

        assert str(raised.value) == f"Owner.flag needs a reason rather than {value!r} - say why, as a string."

    def test_opting_out_with_a_reason_is_fine(self):
        require_reason("Owner", "flag", "the default doesn't apply here")

    def test_a_mapping_of_reasons_is_fine(self):
        require_reasons("Owner", "exempt", {"a": "why a", "b": Exemption("a project's own stricter reason")})

    @pytest.mark.parametrize("reasons", [("a", "b"), ["a"], "a"])
    def test_names_without_reasons_are_refused(self, reasons):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_reasons("Owner", "exempt", reasons)

        assert str(raised.value) == f"Owner.exempt takes {{name: reason}}, not {reasons!r}."

    @pytest.mark.parametrize("reason", ["", " ", None, True])
    def test_a_name_without_its_reason_is_refused(self, reason):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_reasons("Owner", "exempt", {"a": "why", "b": reason})

        assert str(raised.value) == f"Owner.exempt needs a reason for b, not {reason!r}."


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]


class TestWhereItApplies:
    def test_a_viewset_exempt_from_its_registry_says_why(self):
        with pytest.raises(ImproperlyConfigured, match=r"^Bare\.exempt_from_registry needs a reason"):
            type("Bare", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": True})

    def test_a_viewset_exempt_with_a_reason_stays_out_of_the_registry(self):
        reasoned = type("Reasoned", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": "a second one"})

        assert ViewSetRegistryMixin.get_for_model(Widget) is not reasoned

    def test_a_serializer_exempt_from_its_registry_says_why(self):
        with pytest.raises(ImproperlyConfigured, match=r"^Bare\.exempt_from_registry needs a reason"):
            type("Bare", (ModelSerializerRegistryMixin,), {"exempt_from_registry": True})

    def test_unguarded_fields_say_why_each_is_open(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Open", (GuardedFieldsMixin, viewsets.GenericViewSet), {"unguarded_fields": ["count"]})

        assert str(raised.value) == "Open.unguarded_fields takes {name: reason}, not ['count']."

    def test_actions_writing_no_guarded_fields_say_why(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type(
                "Silent",
                (GuardedFieldsMixin, viewsets.GenericViewSet),
                {"actions_writing_no_guarded_fields": {"destroy": "deletes", "touch": ""}},
            )

        assert str(raised.value) == "Silent.actions_writing_no_guarded_fields needs a reason for touch, not ''."

    def test_destroy_is_exempt_and_says_why(self):
        assert GuardedFieldsMixin.actions_writing_no_guarded_fields == {
            "destroy": "deleting a row writes none of its fields"
        }
