"""
A setting whose null_label says what None is: listed as `<kind>_or_null`, None set and forced like any value, and
None held to a bound without the feature - in Python as Postgres' LEAST and GREATEST hold it in SQL.
"""

import pytest

from isik.django.apps.features.declarations import AtLeast, AtMost
from isik.django.apps.features.operations import force_setting, grant, set_setting
from isik.django.apps.features.resolution import Effective, effective, effective_expression, forget
from tests.django.apps.features.api.test_api import REASON, client_for, patch, settings_of
from tests.testapp.models import EmailUser


pytestmark = pytest.mark.django_db


class TestListed:
    def test_a_number_with_its_null_label(self, alice):
        grant(alice, "seats", source="subscription")
        listed, _etag = settings_of(client_for(alice))

        assert listed["max_seats"] | {"may_change": None} == {
            "name": "max_seats",
            "type": "int_or_null",
            "label": "Seats",
            "description": None,
            "value": None,
            "default": None,
            "origin": "default",
            "forced_reason": None,
            "may_change": None,
            "chosen": None,
            "requires_feature": "seats",
            "null_label": "No limit",
            "minimum": 0,
            "maximum": 2147483647,
        }

    def test_a_choice_with_none_among_its_choices(self, alice):
        listed, _etag = settings_of(client_for(alice))

        assert listed["accent"]["type"] == "choice_or_null"
        assert listed["accent"]["choices"] == [
            {"value": "red", "label": "Red"},
            {"value": "blue", "label": "Blue"},
            {"value": None, "label": "The site's own"},
        ]
        assert "null_label" not in listed["accent"]

    def test_a_setting_with_no_null_label_has_none(self, alice):
        listed, _etag = settings_of(client_for(alice))

        assert "null_label" not in listed["max_endpoints"]
        assert listed["theme"]["choices"] == [{"value": "light", "label": "Light"}, {"value": "dark", "label": "Dark"}]


class TestChanged:
    def test_to_a_value_and_back_to_none(self, alice):
        client = client_for(alice)
        _listed, etag = settings_of(client)

        chosen = patch(client, "accent", "choice_or_null", "red", etag)
        cleared = patch(client, "accent", "choice_or_null", None, chosen["ETag"])

        assert (chosen.status_code, chosen.json()["value"]) == (200, "red")
        assert (cleared.status_code, cleared.json()["value"], cleared.json()["origin"]) == (200, None, "default")

    def test_named_as_the_plain_kind_is_refused(self, alice):
        client = client_for(alice)
        _listed, etag = settings_of(client)

        refused = patch(client, "accent", "choice", "red", etag)

        assert refused.status_code == 400
        assert refused.json()["type"] == ["accent holds a choice_or_null."]

    def test_none_where_there_is_no_null_label_is_refused(self, alice):
        client = client_for(alice)
        _listed, etag = settings_of(client)

        assert patch(client, "theme", "choice", None, etag).status_code == 400

    def test_set_and_forced_to_none(self, alice):
        grant(alice, "seats", source="subscription")
        set_setting(alice, "max_seats", 4)
        assert effective(alice, "max_seats") == Effective(4, "own")

        force_setting(alice, "max_seats", None, source="staff", reason=REASON)

        forget(alice)
        assert effective(alice, "max_seats") == Effective(None, "forced", "staff", REASON)

    def test_none_is_held_to_the_bound_without_the_feature(self, alice, bob):
        grant(bob, "seats", source="subscription")

        assert effective(alice, "max_seats") == Effective(10, "limited")
        assert effective(bob, "max_seats") == Effective(None, "default")
        annotated = dict(
            EmailUser.objects.filter(pk__in=[alice.pk, bob.pk])
            .annotate(value=effective_expression("max_seats"))
            .values_list("pk", "value")
        )
        assert annotated == {alice.pk: 10, bob.pk: None}


class TestBounds:
    def test_none_is_the_bound(self):
        assert (AtMost(10).apply(None), AtLeast(10).apply(None)) == (10, 10)

    def test_a_value_is_clamped(self):
        assert (AtMost(10).apply(12), AtMost(10).apply(3), AtLeast(10).apply(3), AtLeast(10).apply(12)) == (
            10,
            3,
            10,
            12,
        )
