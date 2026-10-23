"""
The opt-outs isik insists on a reason for - each an exemption type's, held to its floor and listed by
its rule, a single one and each value of a `{name: reason}` map alike, and written as that type: a bare
string reads as a value, not as a reason.
"""

import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers, viewsets

from isik._internal.reasons import is_reason, made_exemption, require_exemption, require_exemptions
from isik.common.utils.exemptions import declared_exemptions, exemption_types
from isik.django.apps.common.db.concurrency import UnprovenConcurrencyToken
from isik.django.drf.serializers import SerializerRegistryExemption
from isik.django.drf.serializers.registry import ModelSerializerRegistryMixin
from isik.django.drf.viewsets import (
    GuardedFieldsMixin,
    NoPrecondition,
    RequestPolicyExemption,
    UnguardedField,
    ViewSetRegistryExemption,
    ViewSetRegistryMixin,
    WritesNoGuardedFields,
)
from isik.django.drf.viewsets.guarded_fields import _destroy_writes_no_guarded_field
from tests.common.utils.test_exemptions import NoHelpText
from tests.testapp.models import Widget


REASON = "a second viewset over the model, for staff alone"
# Where this module's code says it is - under mutmut, a copied test's cached bytecode can name the
# original file rather than __file__.
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())


class TestIsReason:
    @pytest.mark.parametrize("value", ["why", " a reason ", "as above, for one of them", "x-ray"])
    def test_anything_said_is_a_reason(self, value):
        assert is_reason(value) is True

    @pytest.mark.parametrize("value", ["", "  \n", "n/a", "N/A", " tbd ", "TBD", "-", ".", "x", True, 1, None, ["why"]])
    def test_blank_placeholders_and_non_text_are_not(self, value):
        assert is_reason(value) is False


class TestAMapOfExemptions:
    def test_a_mapping_of_the_type_is_returned_as_it_is(self):
        exemptions = {"a": ViewSetRegistryExemption(reason=REASON), "b": ViewSetRegistryExemption(reason=REASON)}

        assert require_exemptions("Owner", "exempt", exemptions, ViewSetRegistryExemption) is exemptions

    @pytest.mark.parametrize("value", [("a", "b"), ["a"], "a"])
    def test_names_without_their_exemptions_are_refused(self, value):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_exemptions("Owner", "exempt", value, ViewSetRegistryExemption)

        assert str(raised.value) == f"Owner.exempt takes {{name: ViewSetRegistryExemption(reason=...)}}, not {value!r}."

    def test_a_bare_string_is_refused_naming_the_type_to_write(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_exemptions("Owner", "exempt", {"a": REASON}, ViewSetRegistryExemption)

        assert (
            str(raised.value) == "Owner.exempt takes a ViewSetRegistryExemption(reason=...) for a, not a bare string."
        )

    @pytest.mark.parametrize("value", [None, True, NoHelpText(reason=REASON)])
    def test_anything_else_is_refused(self, value):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_exemptions("Owner", "exempt", {"a": value}, ViewSetRegistryExemption)

        assert str(raised.value) == f"Owner.exempt takes a ViewSetRegistryExemption(reason=...) for a, not {value!r}."


class TestASingleOptOut:
    @pytest.mark.parametrize("value", [False, None])
    def test_not_opting_out_needs_no_reason(self, value):
        assert require_exemption("Owner", "flag", value, ViewSetRegistryExemption) is None

    def test_one_made_is_kept(self):
        made = ViewSetRegistryExemption(reason=REASON)

        assert require_exemption("Owner", "flag", made, ViewSetRegistryExemption) is made

    def test_a_bare_string_is_refused_naming_the_type_to_write(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_exemption("Owner", "flag", REASON, ViewSetRegistryExemption)

        assert str(raised.value) == "Owner.flag takes a ViewSetRegistryExemption(reason=...), not a bare string."

    @pytest.mark.parametrize("value", [True, 1, ["why"], NoHelpText(reason=REASON)])
    def test_anything_else_is_refused(self, value):
        with pytest.raises(ImproperlyConfigured) as raised:
            require_exemption("Owner", "flag", value, ViewSetRegistryExemption)

        assert str(raised.value) == f"Owner.flag takes a ViewSetRegistryExemption(reason=...), not {value!r}."


class TestAFactorysReason:
    def test_text_becomes_the_type(self):
        made = made_exemption("factory", "reason", REASON, ViewSetRegistryExemption)

        assert isinstance(made, ViewSetRegistryExemption)
        assert made == REASON

    def test_one_made_is_kept(self):
        made = ViewSetRegistryExemption(reason=REASON)

        assert made_exemption("factory", "reason", made, ViewSetRegistryExemption) is made

    def test_a_reason_under_the_floor_is_refused_naming_where(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            made_exemption("factory", "reason", "short", ViewSetRegistryExemption)

        assert str(raised.value) == (
            "factory.reason - ViewSetRegistryExemption needs a reason of at least 40 characters. A model has one "
            "viewset in the registry, and a second one over it is usually a mistake. Got 'short'."
        )

    def test_isiks_own_rules(self):
        types = exemption_types()

        assert types["isik.viewset-registry"] is ViewSetRegistryExemption
        assert types["isik.serializer-registry"] is SerializerRegistryExemption
        assert types["isik.guarded-fields.writes-none"] is WritesNoGuardedFields
        assert types["isik.precondition"] is NoPrecondition
        assert types["isik.concurrency-token.unproven"] is UnprovenConcurrencyToken
        assert types["isik.guarded-fields.unguarded"] is UnguardedField
        assert types["isik.request-policy"] is RequestPolicyExemption


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]


class TestWhereItApplies:
    def test_a_viewset_exempt_from_its_registry_says_why(self):
        with pytest.raises(ImproperlyConfigured, match=r"^Bare\.exempt_from_registry takes a ViewSetRegistryExemption"):
            type("Bare", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": True})

    def test_a_viewset_exempt_with_its_type_stays_out_of_the_registry_and_is_listed(self):
        made, line = ViewSetRegistryExemption(reason=REASON), sys._getframe().f_lineno
        reasoned = type("Reasoned", (ViewSetRegistryMixin,), {"model": Widget, "exempt_from_registry": made})

        assert ViewSetRegistryMixin.get_for_model(Widget) is not reasoned
        assert reasoned.exempt_from_registry is made
        assert declared_exemptions("isik.viewset-registry")[-1] is made
        assert (made.file, made.line) == (HERE, line)

    def test_a_subclass_inherits_its_parents_exemption_as_it_is(self):
        parent = type(
            "Parent",
            (ViewSetRegistryMixin,),
            {"model": Widget, "exempt_from_registry": ViewSetRegistryExemption(reason=REASON)},
        )
        count = len(declared_exemptions("isik.viewset-registry"))
        child = type("Child", (parent,), {})

        assert child.exempt_from_registry is parent.exempt_from_registry
        assert len(declared_exemptions("isik.viewset-registry")) == count

    def test_a_serializer_exempt_from_its_registry_says_why(self):
        with pytest.raises(
            ImproperlyConfigured, match=r"^Bare\.exempt_from_registry takes a SerializerRegistryExemption"
        ):
            type(
                "Bare",
                (ModelSerializerRegistryMixin,),
                {"exempt_from_registry": "a schema-only serializer, never saved"},
            )

    def test_a_serializer_exempt_with_its_type(self):
        made = SerializerRegistryExemption(reason="a schema-only serializer, never saved or looked up")

        reasoned = type("Reasoned", (ModelSerializerRegistryMixin,), {"exempt_from_registry": made})

        assert reasoned.exempt_from_registry is made

    def test_unguarded_fields_take_their_type(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type(
                "Open",
                (GuardedFieldsMixin, viewsets.GenericViewSet),
                {"unguarded_fields": {"count": "staff may set the count freely, by design"}},
            )

        assert (
            str(raised.value)
            == "Open.unguarded_fields takes a UnguardedField(reason=...) for count, not a bare string."
        )

    def test_actions_writing_no_guarded_fields_take_their_type(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type(
                "Silent",
                (GuardedFieldsMixin, viewsets.GenericViewSet),
                {"actions_writing_no_guarded_fields": {"touch": "touching a row only saves it as it already is"}},
            )

        assert str(raised.value) == (
            "Silent.actions_writing_no_guarded_fields takes a WritesNoGuardedFields(reason=...) for touch, not a bare "
            "string."
        )

    def test_destroy_is_exempt_and_says_why_as_isiks_own(self):
        (made,) = GuardedFieldsMixin.actions_writing_no_guarded_fields.values()

        assert GuardedFieldsMixin.actions_writing_no_guarded_fields == {"destroy": made}
        assert isinstance(made, WritesNoGuardedFields)
        assert made == "deleting a row writes none of its fields"
        assert made.library is True
        assert all(each is not made for each in declared_exemptions("isik.guarded-fields.writes-none"))

    def test_isiks_own_destroy_is_made_where_isik_makes_it(self):
        made = _destroy_writes_no_guarded_field()["destroy"]

        assert made == "deleting a row writes none of its fields"
        assert made.library is True
        assert made.file.endswith("guarded_fields.py")
