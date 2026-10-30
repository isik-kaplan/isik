"""Forced subscriptions through the API: shown with why, refused when changed, and in the kinds catalog."""

import pytest

from isik.django.apps.notifications.audience.helpers import force_subscription, subscribe
from isik.django.apps.notifications.models import NoticeKindPolicy
from tests.django.apps.notifications.api.test_api import client_for
from tests.django.apps.notifications.test_forcing import REASON, row


pytestmark = pytest.mark.django_db


class TestTheApi:
    def test_a_forced_subscription_shows_why_where_its_source_lets_it(self, forceable, alice):
        force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)
        force_subscription(alice, "parcel_shipped", enabled=False, source="account_review", reason=REASON)

        listed = {each["kind"]: each for each in client_for(alice).get("/subscriptions/").json()}

        assert (listed["parcel_flagged"]["forced"], listed["parcel_flagged"]["forced_reason"]) == (True, REASON)
        assert (listed["parcel_shipped"]["forced"], listed["parcel_shipped"]["forced_reason"]) == (True, None)

    def test_a_forced_one_cant_be_changed_or_forgotten(self, forceable, alice):
        forced = force_subscription(alice, "parcel_shipped", enabled=True, source="staff", reason=REASON)
        client = client_for(alice)

        changed = client.post("/subscriptions/", {"kind": "parcel_shipped", "enabled": False}, format="json")
        forgotten = client.delete(f"/subscriptions/{forced.pk}/")

        assert (changed.status_code, changed.json()["code"]) == (409, "subscription_forced")
        assert forgotten.status_code == 409
        assert forgotten.json() == {
            "detail": "This subscription is forced by staff, so it can't be changed.",
            "code": "subscription_forced",
        }
        assert row(alice, "parcel_shipped").forced

    def test_one_not_forced_is_forgotten_as_before(self, forceable, alice):
        subscribe(alice, "parcel_shipped", enabled=False)

        assert client_for(alice).delete(f"/subscriptions/{row(alice, 'parcel_shipped').pk}/").status_code == 204

    def test_the_catalog_says_what_is_forced_for_the_caller(self, forceable, alice):
        force_subscription(alice, "parcel_shipped", enabled=False, source="staff", reason=REASON)
        force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="account_review", reason=REASON)

        listed = {kind["kind"]: kind for kind in client_for(alice).get("/kinds/").json()}

        shipped, flagged = listed["parcel_shipped"], listed["parcel_flagged"]
        assert (shipped["may_change"], shipped["forced"], shipped["forced_reason"]) == (False, True, REASON)
        assert (flagged["may_change"], flagged["forced"], flagged["forced_variants"]) == (True, False, ["lost"])

    def test_the_catalog_says_what_the_caller_gets_now(self, forceable, alice):
        # Forced off over their own on; one variant forced on, the other their own choice; nothing chosen.
        subscribe(alice, "parcel_shipped", enabled=True)
        force_subscription(alice, "parcel_shipped", enabled=False, source="staff", reason=REASON)
        force_subscription(alice, "parcel_flagged", "lost", enabled=True, source="staff", reason=REASON)
        subscribe(alice, "parcel_flagged", "damaged", enabled=True)

        listed = {kind["kind"]: kind for kind in client_for(alice).get("/kinds/").json()}
        mine = {row["kind"]: row for row in client_for(alice).get("/subscriptions/").json() if row["variant"] is None}

        shipped, flagged = listed["parcel_shipped"], listed["parcel_flagged"]
        assert (shipped["enabled"], shipped["forced_enabled"]) == (False, False)
        assert (mine["parcel_shipped"]["enabled"], mine["parcel_shipped"]["forced_enabled"]) == (True, False)
        assert (flagged["enabled"], flagged["enabled_variants"], flagged["forced_enabled"]) == (
            False,
            {"lost": True, "damaged": True},
            None,
        )
        assert listed["parcel_routed"]["enabled_variants"] == {}

    def test_the_catalog_answers_for_the_caller_alone(self, forceable, alice, bob):
        force_subscription(bob, "parcel_shipped", enabled=False, source="staff", reason=REASON)
        subscribe(bob, "parcel_flagged", enabled=True)
        subscribe(alice, "parcel_flagged", "damaged", enabled=False)

        listed = {kind["kind"]: kind for kind in client_for(alice).get("/kinds/").json()}

        assert (listed["parcel_shipped"]["may_change"], listed["parcel_shipped"]["forced"]) == (True, False)
        assert listed["parcel_flagged"]["enabled_variants"] == {"lost": False, "damaged": False}
        assert listed["parcel_flagged"]["forced_variants"] == []

    def test_a_choice_for_every_variant_answers_for_each(self, forceable, alice):
        subscribe(alice, "parcel_flagged", enabled=True)

        flagged = {kind["kind"]: kind for kind in client_for(alice).get("/kinds/").json()}["parcel_flagged"]

        assert (flagged["may_change"], flagged["enabled"], flagged["enabled_variants"]) == (
            True,
            True,
            {"lost": True, "damaged": True},
        )

    def test_a_variant_a_kind_gives_by_default(self, alice):
        NoticeKindPolicy.objects.filter(name="parcel_flagged").update(policy="default_on")
        NoticeKindPolicy.objects.filter(name="parcel_reminder").update(policy="mandatory")

        listed = {kind["kind"]: kind for kind in client_for(alice).get("/kinds/").json()}

        assert listed["parcel_flagged"]["enabled_variants"] == {"lost": True, "damaged": True}
        assert listed["parcel_reminder"]["enabled_variants"] == {"daily": True, "weekly": True}

    def test_the_catalog_publishes_the_fields_a_project_names(self, alice, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_KIND_FIELDS": ["kind", "label"]}

        assert client_for(alice).get("/kinds/parcel_shipped/").json() == {
            "kind": "parcel_shipped",
            "label": "Parcel shipped",
        }

    def test_the_catalog_shows_the_kinds_a_project_lets_a_person_see(self, alice, settings):
        seen = []
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "KINDS_VISIBLE": lambda request: seen.append(request.user) or ["parcel_shipped"],
        }

        assert [kind["kind"] for kind in client_for(alice).get("/kinds/").json()] == ["parcel_shipped"]
        assert seen == [alice]
