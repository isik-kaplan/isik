from dataclasses import replace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import BooleanField, Case, Value, When
from django.test.utils import CaptureQueriesContext

from isik.django.apps.notifications import kinds
from isik.django.apps.notifications.audiences import Audience, Field, Subscribers, could_reach
from isik.django.apps.notifications.kinds import get_kind
from tests.django.apps.notifications.api.test_api import client_for


pytestmark = pytest.mark.django_db


class Nobody(Audience):
    def could_include(self, user):
        return False


class Alices(Audience):
    """Only alice - told from an annotation, as a role kept in another table would be."""

    def annotations(self):
        return {
            "is_alice": Case(
                When(username="alice", then=Value(True)), default=Value(False), output_field=BooleanField()
            )
        }

    def could_include(self, user):
        return user.is_alice


def given(monkeypatch, name, audience):
    # The last declaration of a name is its kind.
    monkeypatch.setattr(kinds, "declared", [*kinds.declared, replace(get_kind(name), audience=audience)])


class TestAnAudience:
    def test_cant_tell_unless_it_says(self):
        assert Audience().could_include(object()) is None
        assert Audience().annotations() == {}
        assert Field("user").could_include(object()) is None

    def test_of_subscribers_includes_anyone(self):
        assert Subscribers().could_include(object()) is True


class TestCouldReach:
    def test_asks_each_kinds_audience(self, alice, monkeypatch):
        given(monkeypatch, "parcel_flagged", Nobody())

        assert could_reach(alice, ["parcel_flagged", "parcel_reminder", "parcel_shipped", "parcel_receipt"]) == {
            "parcel_flagged": False,
            "parcel_reminder": True,
            "parcel_shipped": None,
            "parcel_receipt": None,
        }

    def test_cant_tell_for_an_audience_of_its_own_saying_nothing(self, alice, monkeypatch, django_assert_num_queries):
        class OnlyRecipients:
            def recipients(self, notice):
                return []

        given(monkeypatch, "parcel_flagged", OnlyRecipients())

        with django_assert_num_queries(0):
            assert could_reach(alice, ["parcel_flagged"]) == {"parcel_flagged": None}

    def test_asks_nothing_of_the_database_with_no_annotations(self, alice, django_assert_num_queries):
        with django_assert_num_queries(0):
            could_reach(alice, ["parcel_flagged", "parcel_reminder", "parcel_shipped"])

    def test_loads_the_person_once_with_every_annotation(self, alice, bob, monkeypatch):
        for name in ("parcel_flagged", "parcel_shipped", "parcel_routed"):
            given(monkeypatch, name, Alices())
        names = ["parcel_flagged", "parcel_shipped", "parcel_routed", "parcel_reminder"]

        with CaptureQueriesContext(connection) as queries:
            mine = could_reach(alice, names)
        theirs = could_reach(bob, names)

        assert len(queries) == 1
        assert mine == {"parcel_flagged": True, "parcel_shipped": True, "parcel_routed": True, "parcel_reminder": True}
        assert theirs == {
            "parcel_flagged": False,
            "parcel_shipped": False,
            "parcel_routed": False,
            "parcel_reminder": True,
        }

    def test_reads_the_person_asked_about(self, alice, bob, monkeypatch):
        given(monkeypatch, "parcel_flagged", Alices())
        get_user_model().objects.filter(pk=bob.pk).update(username="alice-not")

        assert could_reach(bob, ["parcel_flagged"]) == {"parcel_flagged": False}


class TestTheKindsListed:
    def test_say_whether_you_could_get_each(self, alice, bob, monkeypatch):
        given(monkeypatch, "parcel_flagged", Alices())
        given(monkeypatch, "parcel_routed", Nobody())

        mine = {kind["kind"]: kind["in_audience"] for kind in client_for(alice).get("/kinds/").json()}
        theirs = client_for(bob).get("/kinds/parcel_flagged/").json()["in_audience"]

        assert (mine["parcel_flagged"], mine["parcel_routed"], mine["parcel_reminder"], mine["parcel_shipped"]) == (
            True,
            False,
            True,
            None,
        )
        assert theirs is False

    def test_ask_one_query_for_reach_however_many_kinds(self, alice, monkeypatch):
        baseline = _queries(client_for(alice))
        for name in ("parcel_flagged", "parcel_shipped", "parcel_routed"):
            given(monkeypatch, name, Alices())

        assert _queries(client_for(alice)) == baseline + 1


def _queries(client):
    with CaptureQueriesContext(connection) as queries:
        assert client.get("/kinds/").status_code == 200
    return len(queries)


class TestASubscription:
    def test_to_a_kind_you_arent_among_the_audience_of_is_refused(self, alice, monkeypatch):
        from isik.django.apps.notifications.audience.swapping import get_subscription_model

        given(monkeypatch, "parcel_flagged", Nobody())

        answered = client_for(alice).post("/subscriptions/", {"kind": "parcel_flagged"}, format="json")

        assert answered.status_code == 409
        assert answered.json() == {
            "detail": "parcel_flagged is sent to people you aren't among, so you can't choose whether you get it.",
            "code": "not_in_audience",
        }
        assert not get_subscription_model().objects.exists()

    def test_to_a_kind_that_cant_tell_is_taken(self, alice, monkeypatch):
        given(monkeypatch, "parcel_flagged", Alices())

        assert client_for(alice).post("/subscriptions/", {"kind": "parcel_flagged"}, format="json").status_code == 201
