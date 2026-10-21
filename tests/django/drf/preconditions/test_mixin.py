"""PreconditionMixin's parts on their own - how a class declares it, and how a header is read."""

import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from rest_framework import serializers
from rest_framework.viewsets import GenericViewSet

from isik.common.utils.exemptions import declared_exemptions
from isik.django.drf.serializers import ConcurrencyTokenField
from isik.django.drf.viewsets import BaseModelViewSet, NoPrecondition, PreconditionMixin
from isik.django.drf.viewsets.preconditions import entity_tags, matches, requires_precondition
from tests.django.drf.preconditions.views import WidgetViewSet
from tests.testapp.models import VersionedWidget, Widget


# Where this module's code says it is - under mutmut, a copied test's cached bytecode can name the
# original file rather than __file__.
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())
REASON = "a rename that may land over any other write, by design"


class TestEntityTags:
    def test_a_list_is_split_on_commas_and_trimmed(self):
        assert entity_tags(' "a" ,"b",  W/"c" ') == {'"a"', '"b"', '"c"'}

    def test_a_weak_prefix_is_dropped(self):
        assert entity_tags('W/"a"') == {'"a"'}

    def test_only_a_leading_weak_prefix_is(self):
        assert entity_tags('"W/a"') == {'"W/a"'}


class TestMatches:
    @pytest.mark.parametrize("header", ['"a"', 'W/"a"', '"b", "a"', "*", '"b", *'])
    def test_naming_the_tag_or_any(self, header):
        assert matches(header, '"a"')

    @pytest.mark.parametrize("header", ['"b"', "a", '"a "', ""])
    def test_anything_else(self, header):
        assert not matches(header, '"a"')


class TestWhatNeedsIfMatch:
    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_a_write_to_one_object(self, method):
        assert requires_precondition(PreconditionMixin, method, "update", None)

    def test_a_post_to_one_object(self):
        assert requires_precondition(PreconditionMixin, "POST", "bump", True)

    @pytest.mark.parametrize("detail", [False, None])
    def test_not_a_post_to_no_one_object(self, detail):
        assert not requires_precondition(PreconditionMixin, "POST", "create", detail)

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_not_a_read(self, method):
        assert not requires_precondition(PreconditionMixin, method, "retrieve", True)

    def test_not_an_exempt_action(self):
        assert not requires_precondition(WidgetViewSet, "POST", "rename_freely", True)
        assert requires_precondition(WidgetViewSet, "POST", "bump", True)

    def test_not_a_method_the_view_leaves_out(self):
        view = type("NoDeletes", (PreconditionMixin,), {"precondition_methods": ("PUT", "PATCH")})

        assert not requires_precondition(view, "DELETE", "destroy", True)
        assert requires_precondition(view, "PATCH", "partial_update", True)


class TestExemptActions:
    def test_each_reason_becomes_a_listed_exemption(self):
        view, line = (
            type("Exempting", (PreconditionMixin, GenericViewSet), {"precondition_exempt_actions": {"touch": REASON}}),
            sys._getframe().f_lineno,
        )

        made = view.precondition_exempt_actions["touch"]
        assert isinstance(made, NoPrecondition)
        assert made == REASON
        assert declared_exemptions("isik.precondition")[-1] is made
        assert made.file == HERE
        assert made.line in (line - 1, line)

    def test_one_already_made_is_kept(self):
        made = NoPrecondition(reason=REASON)

        view = type("Made", (PreconditionMixin,), {"precondition_exempt_actions": {"touch": made}})

        assert view.precondition_exempt_actions["touch"] is made

    def test_a_mapping_is_required(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Listed", (PreconditionMixin,), {"precondition_exempt_actions": ["touch"]})

        assert str(raised.value) == "Listed.precondition_exempt_actions takes {name: reason}, not ['touch']."

    def test_a_missing_reason_is_refused(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Blank", (PreconditionMixin,), {"precondition_exempt_actions": {"touch": None}})

        assert str(raised.value) == "Blank.precondition_exempt_actions needs a reason for touch, not None."

    def test_a_short_reason_is_refused(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("Short", (PreconditionMixin,), {"precondition_exempt_actions": {"touch": "by design"}})

        assert str(raised.value).startswith(
            "Short.precondition_exempt_actions - NoPrecondition needs a reason of at least 40 characters."
        )

    def test_none_by_default(self):
        assert PreconditionMixin.precondition_exempt_actions == {}


class TestTheModel:
    def test_named_outright(self):
        view = type("Named", (PreconditionMixin, GenericViewSet), {"precondition_model": VersionedWidget})

        assert view.get_precondition_model() is VersionedWidget

    def test_named_over_the_others(self):
        view = type(
            "Over",
            (PreconditionMixin, GenericViewSet),
            {"precondition_model": VersionedWidget, "queryset": Widget.objects.all()},
        )

        assert view.get_precondition_model() is VersionedWidget

    def test_a_model_viewsets_own(self):
        view = type(
            "Modeled",
            (PreconditionMixin, BaseModelViewSet),
            {
                "model": VersionedWidget,
                "endpoint": "x",
                "serializer_class": serializers.Serializer,
                "exempt_from_registry": "only here to read its model back, never routed or looked up",
            },
        )

        assert view.get_precondition_model() is VersionedWidget

    def test_the_querysets(self):
        assert WidgetViewSet.get_precondition_model() is Widget

    def test_none_on_a_class_that_isnt_a_drf_view(self):
        assert type("Plain", (PreconditionMixin,), {}).get_precondition_model() is None

    def test_none_to_be_found(self):
        assert type("Bare", (PreconditionMixin, GenericViewSet), {}).get_precondition_model() is None


class TestTheLock:
    @pytest.mark.django_db
    def test_a_row_gone_before_the_lock_is_a_404(self):
        widget = Widget.objects.create(name="bolt")
        Widget.objects.filter(pk=widget.pk).delete()

        with pytest.raises(Http404):
            WidgetViewSet()._locked(widget, '"stale"')


class TestConcurrencyTokenField:
    def test_is_read_only_over_the_whole_object(self):
        field = ConcurrencyTokenField(source="name", read_only=False)

        assert (field.source, field.read_only) == ("*", True)

    def test_keeps_its_other_arguments(self):
        assert ConcurrencyTokenField(help_text="The version.").help_text == "The version."

    def test_a_value_sent_for_it_is_ignored(self):
        class Serializer(serializers.ModelSerializer):
            etag = ConcurrencyTokenField()

            class Meta:
                model = VersionedWidget
                fields = ["name", "etag"]

        serializer = Serializer(data={"name": "bolt", "etag": '"9"'})

        assert serializer.is_valid()
        assert serializer.validated_data == {"name": "bolt"}

    def test_shows_the_etag(self):
        assert ConcurrencyTokenField().to_representation(VersionedWidget(version=3)) == '"3"'
