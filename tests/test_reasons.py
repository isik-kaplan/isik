"""
The opt-outs isik insists on a reason for. A single one - a registry exemption, an action writing no
guarded field - is an exemption type's, held to its floor and listed by its rule. A `{name: reason}`
map needs no floor, only something more than blank or a placeholder.
"""

import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers, viewsets

from isik._internal.reasons import is_reason, require_exemption, require_reasons
from isik.common.utils.exemptions import declared_exemptions, exemption_types
from isik.django.drf.serializers import SerializerRegistryExemption
from isik.django.drf.serializers.registry import ModelSerializerRegistryMixin
from isik.django.drf.viewsets import (
    GuardedFieldsMixin,
    ViewSetRegistryExemption,
    ViewSetRegistryMixin,
    WritesNoGuardedFields,
)
from tests.common.utils.test_exemptions import NoHelpText
from tests.testapp.models import Widget


REASON = "a second viewset over the model, for staff alone"
# Where this module's code says it is - under mutmut, a copied test's cached bytecode can name the
# original file rather than __file__.
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())


class TestAReasonInAMap:
    @pytest.mark.parametrize("value", ["why", " a reason ", "as above, for one of them", "x-ray"])
    def test_anything_said_is_a_reason(self, value):
        assert is_reason(value) is True

    @pytest.mark.parametrize("value", ["", "  \n", "n/a", "N/A", " tbd ", "TBD", "-", ".", "x", True, 1, None, ["why"]])
    def test_blank_placeholders_and_non_text_are_not(self, value):
        assert is_reason(value) is False

    def test_a_mapping_of_reasons_is_fine(self):
        require_reasons("Owner", "exempt", {"a": "why a", "b": "reading the row"})

    @pytest.mark.parametrize("reasons", [("a", "b"), ["a"], "a"])
    def test_names_without_reasons_are_refused(self, reasons):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_reasons("Owner", "exempt", reasons)

        assert str(raised.value) == f"Owner.exempt takes {{name: reason}}, not {reasons!r}."

    @pytest.mark.parametrize("reason", ["", " ", "n/a", None, True])
    def test_a_name_without_its_reason_is_refused(self, reason):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_reasons("Owner", "exempt", {"a": "why", "b": reason})

        assert str(raised.value) == f"Owner.exempt needs a reason for b, not {reason!r}."


class TestASingleOptOut:
    @pytest.mark.parametrize("value", [False, None])
    def test_not_opting_out_needs_no_reason(self, value):
        assert require_exemption("Owner", "flag", value, ViewSetRegistryExemption) is None

    def test_a_reason_becomes_the_sites_exemption(self):
        made = require_exemption("Owner", "flag", REASON, ViewSetRegistryExemption)

        assert isinstance(made, ViewSetRegistryExemption)
        assert made == made.reason == REASON

    def test_one_already_made_is_kept(self):
        made = ViewSetRegistryExemption(reason=REASON)

        assert require_exemption("Owner", "flag", made, ViewSetRegistryExemption) is made

    @pytest.mark.parametrize("value", ["", "short", "n/a"])
    def test_a_reason_under_the_floor_is_refused_with_the_types_rule(self, value):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_exemption("Owner", "flag", value, ViewSetRegistryExemption)

        assert str(raised.value) == (
            "Owner.flag - ViewSetRegistryExemption needs a reason of at least 40 characters. A model has one "
            f"viewset in the registry, and a second one over it is usually a mistake. Got {value!r}."
        )

    @pytest.mark.parametrize("value", [True, 1, ["why"], NoHelpText(reason=REASON)])
    def test_anything_else_is_refused(self, value):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_exemption("Owner", "flag", value, ViewSetRegistryExemption)

        assert str(raised.value) == (
            f"Owner.flag takes a ViewSetRegistryExemption, or its reason as a str, not {value!r}."
        )

    def test_isiks_own_rules(self):
        types = exemption_types()

        assert types["isik.viewset-registry"] is ViewSetRegistryExemption
        assert types["isik.serializer-registry"] is SerializerRegistryExemption
        assert types["isik.guarded-fields.writes-none"] is WritesNoGuardedFields


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]


class TestWhereItApplies:
    def test_a_viewset_exempt_from_its_registry_says_why(self):
        with pytest.raises(ImproperlyConfigured, match=r"^Bare\.exempt_from_registry takes a ViewSetRegistryExemption"):
            type("Bare", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": True})

    def test_a_short_reason_is_refused_where_the_class_is_defined(self):
        with pytest.raises(
            ImproperlyConfigured, match=r"^Short\.exempt_from_registry - ViewSetRegistryExemption needs"
        ):
            type("Short", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": "n/a"})

    def test_a_viewset_exempt_with_a_reason_stays_out_of_the_registry_and_is_listed(self):
        reasoned, line = (
            type("Reasoned", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": REASON}),
            sys._getframe().f_lineno,
        )

        assert ViewSetRegistryMixin.get_for_model(Widget) is not reasoned
        assert isinstance(reasoned.exempt_from_registry, ViewSetRegistryExemption)
        made = declared_exemptions("isik.viewset-registry")[-1]
        assert made is reasoned.exempt_from_registry
        assert made.file == HERE
        assert made.line in (line - 1, line)

    def test_a_subclass_inherits_its_parents_exemption_as_it_is(self):
        parent = type("Parent", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": REASON})
        count = len(declared_exemptions("isik.viewset-registry"))
        child = type("Child", (parent,), {})

        assert child.exempt_from_registry is parent.exempt_from_registry
        assert len(declared_exemptions("isik.viewset-registry")) == count

    def test_a_serializer_exempt_from_its_registry_says_why(self):
        with pytest.raises(
            ImproperlyConfigured, match=r"^Bare\.exempt_from_registry takes a SerializerRegistryExemption"
        ):
            type("Bare", (ModelSerializerRegistryMixin,), {"exempt_from_registry": True})

    def test_a_serializer_exempt_with_a_reason_is_its_types(self):
        reasoned = type(
            "Reasoned",
            (ModelSerializerRegistryMixin,),
            {
                "exempt_from_registry": SerializerRegistryExemption(
                    reason="a schema-only serializer, never saved or looked up"
                )
            },
        )

        assert isinstance(reasoned.exempt_from_registry, SerializerRegistryExemption)

    def test_unguarded_fields_say_why_each_is_open(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Open", (GuardedFieldsMixin, viewsets.GenericViewSet), {"unguarded_fields": ["count"]})

        assert str(raised.value) == "Open.unguarded_fields takes {name: reason}, not ['count']."

    def test_actions_writing_no_guarded_fields_say_why(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type(
                "Silent",
                (GuardedFieldsMixin, viewsets.GenericViewSet),
                {"actions_writing_no_guarded_fields": {"destroy": "deletes", "touch": "n/a"}},
            )

        assert str(raised.value) == "Silent.actions_writing_no_guarded_fields needs a reason for touch, not 'n/a'."

    def test_destroy_is_exempt_and_says_why(self):
        assert GuardedFieldsMixin.actions_writing_no_guarded_fields == {
            "destroy": "deleting a row writes none of its fields"
        }
