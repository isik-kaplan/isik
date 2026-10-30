"""The notifications' REST views - the inbox, subscriptions and endpoints - through the real handler."""

import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from isik.django.apps.idempotency.coverage import idempotency_coverage
from isik.django.apps.notifications.audience.helpers import subscribe
from isik.django.apps.notifications.audience.swapping import (
    get_endpoint_kind_model,
    get_endpoint_model,
    get_inbox_item_model,
    get_subscription_model,
)
from isik.django.drf.coverage import CoverageStatus, precondition_coverage
from tests.django.apps.notifications import kind_named


pytestmark = pytest.mark.django_db


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def keyed(**headers):
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()), **headers}


def item(user, kind="parcel_shipped", **fields):
    made = {"payload": {"data": {"n": 1}}, **fields}
    return get_inbox_item_model().objects.create(user=user, kind=kind_named(kind), delivery_id=uuid.uuid4(), **made)


class TestTheInbox:
    def test_it_lists_the_callers_own_newest_first(self, alice, bob):
        first, second = item(alice), item(alice, variant=None, read_at=timezone.now())
        item(bob)

        listed = client_for(alice).get("/inbox/").json()

        assert [each["id"] for each in listed] == [str(second.pk), str(first.pk)]
        assert listed[1] == {
            "id": str(first.pk),
            "kind": "parcel_shipped",
            "variant": None,
            # Its data lacks what parcel_shipped's message names: an item older than its text reads unfilled.
            "title": None,
            "message": None,
            "subject_label": None,
            "subject_id": None,
            "payload": {"data": {"n": 1}},
            "read_at": None,
            "created_at": listed[1]["created_at"],
        }

    def test_an_item_reads_as_its_kinds_sentence(self, alice):
        flagged = item(alice, kind="parcel_flagged", variant="lost", payload={"data": {"status": "lost"}})

        shown = client_for(alice).get(f"/inbox/{flagged.pk}/").json()

        assert (shown["title"], shown["message"]) == ("A parcel was lost", "A parcel you sent is lost.")

    def test_an_item_without_its_data_reads_as_no_sentence(self, alice):
        bare = item(alice, kind="parcel_flagged", variant="lost", payload={})

        shown = client_for(alice).get(f"/inbox/{bare.pk}/").json()

        assert (shown["title"], shown["message"]) == (None, None)

    def test_a_callable_message_reads_the_data_and_the_variant(self, alice):
        reminded = item(alice, kind="parcel_reminder", variant="daily", payload={"data": {"name": "Box"}})

        shown = client_for(alice).get(f"/inbox/{reminded.pk}/").json()

        assert (shown["title"], shown["message"]) == ("Daily reminder", "Your daily reminder: Box.")

    @pytest.mark.parametrize("flag", ["true", "1"])
    def test_unread_only(self, alice, flag):
        unread, _read = item(alice), item(alice, read_at=timezone.now())

        assert [each["id"] for each in client_for(alice).get(f"/inbox/?unread={flag}").json()] == [str(unread.pk)]
        assert len(client_for(alice).get("/inbox/?unread=no").json()) == 2

    def test_one_item(self, alice, bob):
        mine = item(alice)

        assert client_for(alice).get(f"/inbox/{mine.pk}/").json()["id"] == str(mine.pk)
        assert client_for(bob).get(f"/inbox/{mine.pk}/").status_code == 404

    def test_marking_one_read_needs_no_key_or_version(self, alice):
        mine = item(alice)

        answered = client_for(alice).post(f"/inbox/{mine.pk}/read/")

        assert answered.status_code == 200
        assert answered.json()["read_at"] is not None
        mine.refresh_from_db()
        assert mine.read_at is not None
        again = client_for(alice).post(f"/inbox/{mine.pk}/read/").json()["read_at"]
        assert again == answered.json()["read_at"]

    def test_marking_all_read(self, alice, bob):
        item(alice)
        item(alice)
        theirs = item(bob)

        assert client_for(alice).post("/inbox/read-all/").json() == {"marked": 2}
        theirs.refresh_from_db()
        assert theirs.read_at is None

    def test_signed_out_is_refused(self):
        assert client_for().get("/inbox/").status_code in (401, 403)


class TestSubscriptions:
    def test_setting_one(self, alice):
        answered = client_for(alice).post(
            "/subscriptions/", {"kind": "parcel_flagged", "variant": "lost", "enabled": True}, format="json"
        )

        assert answered.status_code == 201
        row = get_subscription_model().objects.get(user=alice)
        assert answered.json() == {
            "id": str(row.pk),
            "kind": "parcel_flagged",
            "variant": "lost",
            "enabled": True,
            "forced": False,
            "forced_enabled": None,
            "forced_reason": None,
        }

    def test_setting_every_variant_replaces_the_named_ones(self, alice):
        client = client_for(alice)
        client.post("/subscriptions/", {"kind": "parcel_flagged", "variant": "lost"}, format="json")

        client.post("/subscriptions/", {"kind": "parcel_flagged", "enabled": False}, format="json")

        assert list(get_subscription_model().objects.values_list("variant", "enabled")) == [(None, False)]

    def test_a_variant_beside_every_one_is_refused(self, alice):
        client = client_for(alice)
        client.post("/subscriptions/", {"kind": "parcel_flagged"}, format="json")

        answered = client.post("/subscriptions/", {"kind": "parcel_flagged", "variant": "lost"}, format="json")

        assert answered.status_code == 400
        assert answered.json() == {
            "variant": [
                "alice@example.com has a subscription to every variant of parcel_flagged - unsubscribe it before "
                "naming one."
            ]
        }

    def test_an_undeclared_variant_or_kind_is_refused(self, alice):
        client = client_for(alice)

        assert client.post(
            "/subscriptions/", {"kind": "parcel_flagged", "variant": "stolen"}, format="json"
        ).json() == {"variant": ["parcel_flagged declares no variant 'stolen'."]}
        assert client.post("/subscriptions/", {"kind": "nothing"}, format="json").status_code == 400

    def test_a_retired_kind_is_refused(self, alice):
        from isik.django.apps.notifications.models import NoticeKindPolicy

        NoticeKindPolicy.objects.filter(name="parcel_flagged").update(retired_at=timezone.now())

        assert client_for(alice).post("/subscriptions/", {"kind": "parcel_flagged"}, format="json").status_code == 400

    def test_a_mandatory_kind_cant_be_chosen(self, alice):
        from isik.django.apps.notifications.models import NoticeKindPolicy

        NoticeKindPolicy.objects.filter(name="parcel_flagged").update(policy="mandatory")

        answered = client_for(alice).post("/subscriptions/", {"kind": "parcel_flagged"}, format="json")

        assert answered.status_code == 409
        assert answered.json() == {
            "detail": "parcel_flagged is mandatory - it reaches everyone it's for.",
            "code": "kind_mandatory",
        }

    def test_listing_and_forgetting_the_callers_own(self, alice, bob):
        from isik.django.apps.notifications.audience.helpers import subscribe

        mine = subscribe(alice, "parcel_flagged", "lost")
        subscribe(alice, "parcel_shipped", enabled=False)
        theirs = subscribe(bob, "parcel_flagged")

        listed = client_for(alice).get("/subscriptions/").json()
        assert [(each["kind"], each["variant"]) for each in listed] == [
            ("parcel_flagged", "lost"),
            ("parcel_shipped", None),
        ]
        assert client_for(alice).delete(f"/subscriptions/{mine.pk}/").status_code == 204
        assert client_for(alice).delete(f"/subscriptions/{theirs.pk}/").status_code == 404
        assert get_subscription_model().objects.filter(user=alice).count() == 1

    def test_every_kind_a_caller_may_get_mandatory_ones_too(self, alice):
        from isik.django.apps.notifications.models import NoticeKindPolicy

        NoticeKindPolicy.objects.create(
            name="undeclared", policy="default_on", scope="single_partition", source="trigger"
        )
        NoticeKindPolicy.objects.filter(name="parcel_reminder").update(policy="mandatory")

        listed = {kind["kind"]: kind for kind in client_for(alice).get("/kinds/").json()}

        assert sorted(listed) == [
            "parcel_flagged",
            "parcel_receipt",
            "parcel_reminder",
            "parcel_routed",
            "parcel_shipped",
            "subscription_forced",
        ]
        assert listed["parcel_flagged"] == {
            "kind": "parcel_flagged",
            "label": "Parcel flagged",
            "description": None,
            "policy": "default_off",
            "variants": ["lost", "damaged"],
            "channels": ["inbox", "email", "webhook"],
            "may_change": True,
            "enabled": False,
            "enabled_variants": {"lost": False, "damaged": False},
            "forced": False,
            "forced_enabled": None,
            "forced_variants": [],
            "forced_reason": None,
        }
        assert (listed["parcel_reminder"]["policy"], listed["parcel_reminder"]["may_change"]) == ("mandatory", False)
        assert listed["parcel_reminder"]["enabled"] is True

    def test_one_kind_by_its_name(self, alice):
        assert client_for(alice).get("/kinds/parcel_shipped/").json()["description"] == (
            "A parcel you sent or will receive is on its way."
        )
        assert client_for(alice).get("/kinds/nothing/").status_code == 404


@pytest.fixture
def endpoint():
    found = get_endpoint_model().objects.create(url="https://hooks.example/in", secret="old")
    get_endpoint_kind_model().objects.create(endpoint=found, kind=kind_named("parcel_flagged"), variant="lost")
    return found


def version_of(client, endpoint):
    return client.get(f"/endpoints/{endpoint.pk}/")["ETag"]


class TestEndpoints:
    def test_creating_one_shows_its_secret_once(self, alice):
        client = client_for(alice)

        answered = client.post(
            "/endpoints/",
            {
                "url": "https://hooks.example/new",
                "kinds": [{"kind": "parcel_flagged", "variant": "lost"}, {"kind": "parcel_shipped"}],
            },
            format="json",
            **keyed(),
        )

        assert answered.status_code == 201
        created = get_endpoint_model().objects.get(url="https://hooks.example/new")
        assert answered.json()["secret"] == created.secret
        assert len(created.secret) >= 32
        assert answered.json()["kinds"] == [
            {"kind": "parcel_flagged", "variant": "lost"},
            {"kind": "parcel_shipped", "variant": None},
        ]
        assert client.get(f"/endpoints/{created.pk}/").json()["secret"] is None

    def test_a_kind_it_wants_twice_over_is_refused(self, alice):
        answered = client_for(alice).post(
            "/endpoints/",
            {
                "url": "https://hooks.example/new",
                "kinds": [{"kind": "parcel_flagged"}, {"kind": "parcel_flagged", "variant": "lost"}],
            },
            format="json",
            **keyed(),
        )

        assert answered.json() == {
            "kinds": [
                "parcel_flagged is wanted for every variant and for some - name it once, with no variant, for all."
            ]
        }
        assert not get_endpoint_model().objects.filter(url="https://hooks.example/new").exists()

    def test_a_kind_named_twice_is_refused(self, alice):
        twice = [{"kind": "parcel_flagged", "variant": "lost"}, {"kind": "parcel_flagged", "variant": "lost"}]

        answered = client_for(alice).post(
            "/endpoints/", {"url": "https://hooks.example/new", "kinds": twice}, format="json", **keyed()
        )

        assert answered.json() == {"kinds": ["parcel_flagged is named twice."]}

    def test_an_undeclared_variant_is_refused(self, alice):
        answered = client_for(alice).post(
            "/endpoints/",
            {"url": "https://hooks.example/new", "kinds": [{"kind": "parcel_flagged", "variant": "stolen"}]},
            format="json",
            **keyed(),
        )

        assert answered.json() == {"kinds": [{"variant": ["parcel_flagged declares no variant 'stolen'."]}]}

    def test_changing_one_needs_its_version(self, alice, endpoint):
        client = client_for(alice)
        change = {"kinds": [{"kind": "parcel_shipped"}]}

        assert client.patch(f"/endpoints/{endpoint.pk}/", change, format="json", **keyed()).status_code == 428

        answered = client.patch(
            f"/endpoints/{endpoint.pk}/", change, format="json", **keyed(HTTP_IF_MATCH=version_of(client, endpoint))
        )
        assert answered.status_code == 200
        assert answered.json()["kinds"] == [{"kind": "parcel_shipped", "variant": None}]

    def test_a_change_leaving_kinds_out_keeps_them(self, alice, endpoint):
        client = client_for(alice)

        answered = client.patch(
            f"/endpoints/{endpoint.pk}/",
            {"is_active": False},
            format="json",
            **keyed(HTTP_IF_MATCH=version_of(client, endpoint)),
        )

        assert answered.json()["is_active"] is False
        assert answered.json()["kinds"] == [{"kind": "parcel_flagged", "variant": "lost"}]

    def test_rotating_its_secret_keeps_the_old_one_signing_until_finished(self, alice, endpoint):
        client = client_for(alice)

        rotated = client.post(
            f"/endpoints/{endpoint.pk}/rotate-secret/", **keyed(HTTP_IF_MATCH=version_of(client, endpoint))
        )

        endpoint.refresh_from_db()
        assert rotated.status_code == 200
        assert rotated.json()["secret"] == endpoint.secret != "old"
        assert endpoint.secret_previous == "old"
        finished = client.post(
            f"/endpoints/{endpoint.pk}/finish-rotation/", **keyed(HTTP_IF_MATCH=version_of(client, endpoint))
        )
        endpoint.refresh_from_db()
        assert (finished.json()["secret"], endpoint.secret_previous) == (None, None)

    def test_unparking_one(self, alice, endpoint):
        get_endpoint_model().objects.filter(pk=endpoint.pk).update(parked_at=timezone.now(), consecutive_failures=10)
        client = client_for(alice)

        answered = client.post(f"/endpoints/{endpoint.pk}/unpark/", **keyed(HTTP_IF_MATCH=version_of(client, endpoint)))

        assert (answered.json()["parked_at"], answered.json()["consecutive_failures"]) == (None, 0)

    def test_deleting_one(self, alice, endpoint):
        client = client_for(alice)

        answered = client.delete(f"/endpoints/{endpoint.pk}/", **keyed(HTTP_IF_MATCH=version_of(client, endpoint)))

        assert answered.status_code == 204
        assert not get_endpoint_model().objects.exists()

    def test_whose_they_are_is_the_projects_to_say(self, alice):
        with pytest.raises(
            ImproperlyConfigured,
            match=(
                "^EndpointViewSet doesn't know whose endpoints a caller sees - set its owner_field, or override "
                "get_queryset\\(\\).$"
            ),
        ):
            client_for(alice).get("/unscoped-endpoints/")


def statuses(report):
    return {(entry.routed.view.__name__, entry.routed.action, entry.routed.method): entry.status for entry in report}


def test_every_routed_write_is_keyed_or_says_why():
    report = statuses(
        idempotency_coverage("tests.django.apps.notifications.api.urls", ("POST", "PUT", "PATCH", "DELETE"))
    )

    assert CoverageStatus.UNCOVERED not in report.values()
    assert (
        report[("InboxViewSet", "read", "POST")]
        == report[("SubscriptionViewSet", "create", "POST")]
        == (CoverageStatus.EXEMPT)
    )
    assert report[("EveryEndpointViewSet", "rotate_secret", "POST")] == CoverageStatus.COVERED
    assert report[("OwnedDeliveryViewSet", "retry", "POST")] == CoverageStatus.COVERED


def test_every_routed_write_to_one_object_asks_its_version_or_says_why():
    report = statuses(precondition_coverage("tests.django.apps.notifications.api.urls"))

    assert CoverageStatus.UNCOVERED not in report.values()
    assert (
        report[("InboxViewSet", "read", "POST")]
        == report[("SubscriptionViewSet", "destroy", "DELETE")]
        == (CoverageStatus.EXEMPT)
    )
    assert report[("EveryEndpointViewSet", "partial_update", "PATCH")] == CoverageStatus.COVERED
    assert report[("EveryEndpointViewSet", "unpark", "POST")] == CoverageStatus.COVERED
    assert report[("OwnedDeliveryViewSet", "retry", "POST")] == CoverageStatus.EXEMPT


class TestTheDetails:
    def test_setting_the_same_subscription_twice_sets_it(self, alice):
        client = client_for(alice)
        choice = {"kind": "parcel_flagged", "variant": "lost"}

        assert client.post("/subscriptions/", choice, format="json").status_code == 201
        again = client.post("/subscriptions/", {**choice, "enabled": False}, format="json")

        assert again.status_code == 201
        assert list(get_subscription_model().objects.values_list("variant", "enabled")) == [("lost", False)]

    def test_a_subscription_is_on_unless_said_otherwise(self, alice):
        assert client_for(alice).post("/subscriptions/", {"kind": "parcel_flagged"}, format="json").json()["enabled"]

    def test_subscriptions_are_listed_by_kind_then_variant(self, alice):
        rows = get_subscription_model().objects
        for kind, variant in [("parcel_reminder", "daily"), ("parcel_flagged", "lost"), ("parcel_flagged", "damaged")]:
            rows.create(user=alice, kind=kind_named(kind), variant=variant)

        listed = client_for(alice).get("/subscriptions/").json()

        assert [(each["kind"], each["variant"]) for each in listed] == [
            ("parcel_flagged", "damaged"),
            ("parcel_flagged", "lost"),
            ("parcel_reminder", "daily"),
        ]

    def test_a_retired_kind_isnt_offered(self, alice):
        from isik.django.apps.notifications.models import NoticeKindPolicy

        NoticeKindPolicy.objects.filter(name="parcel_flagged").update(retired_at=timezone.now())

        offered = [each["kind"] for each in client_for(alice).get("/kinds/").json()]

        assert "parcel_flagged" not in offered

    def test_an_endpoint_wanting_nothing_yet(self, alice):
        answered = client_for(alice).post(
            "/endpoints/", {"url": "https://hooks.example/none"}, format="json", **keyed()
        )

        assert answered.status_code == 201
        assert set(answered.json()) == {
            "id",
            "url",
            "is_active",
            "kinds",
            "consecutive_failures",
            "parked_at",
            "secret",
            "updated_at",
        }
        assert answered.json()["kinds"] == []
        assert len(answered.json()["secret"]) == 43

    def test_an_endpoints_kinds_are_listed_by_kind_then_variant(self, alice):
        endpoint = get_endpoint_model().objects.create(url="https://hooks.example/in", secret="s")
        for kind, variant in [("parcel_reminder", "daily"), ("parcel_flagged", "lost"), ("parcel_flagged", "damaged")]:
            get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named(kind), variant=variant)

        listed = client_for(alice).get(f"/endpoints/{endpoint.pk}/").json()["kinds"]

        assert listed == [
            {"kind": "parcel_flagged", "variant": "damaged"},
            {"kind": "parcel_flagged", "variant": "lost"},
            {"kind": "parcel_reminder", "variant": "daily"},
        ]

    def test_an_endpoints_counters_cant_be_written(self, alice, endpoint):
        client = client_for(alice)

        answered = client.patch(
            f"/endpoints/{endpoint.pk}/",
            {"consecutive_failures": 9, "parked_at": "2026-01-01T00:00:00Z"},
            format="json",
            **keyed(HTTP_IF_MATCH=version_of(client, endpoint)),
        )

        assert (answered.json()["consecutive_failures"], answered.json()["parked_at"]) == (0, None)


class TestOwnedEndpoints:
    def test_a_caller_sees_their_own_and_a_new_one_is_theirs(self, alice, bob):
        endpoints = get_endpoint_model().objects
        mine = endpoints.create(url="https://alice.example/hook", secret="s")
        endpoints.create(url="https://bob.example/hook", secret="s")

        assert [each["id"] for each in client_for(alice).get("/owned-endpoints/").json()] == [str(mine.pk)]
        created = client_for(bob).post(
            "/owned-endpoints/", {"url": "https://anything.example/"}, format="json", **keyed()
        )
        assert created.json()["url"] == "https://bob.example/hook"

    def test_no_owner_field_and_no_queryset_is_refused(self, alice):
        with pytest.raises(ImproperlyConfigured, match="^DeliveryViewSet doesn't know whose endpoints"):
            client_for(alice).get("/unscoped-deliveries/")


@pytest.fixture
def hooked_delivery(alice, bob, dispatched, django_capture_on_commit_callbacks):
    """An abandoned webhook delivery to alice's endpoint, beside one to bob's."""
    from isik.django.apps.notifications import pipeline
    from isik.django.apps.notifications.models import Delivery, DeliveryState
    from tests.django.apps.notifications.test_pipeline import fan_out, flag

    for owner in ("alice", "bob"):
        endpoint = get_endpoint_model().objects.create(url=f"https://{owner}.example/hook", secret="s")
        get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))
    notice = flag(alice)
    fan_out(django_capture_on_commit_callbacks)
    mine = Delivery.objects.get(
        notice=notice, webhook__endpoint_id=str(get_endpoint_model().objects.get(url__startswith="https://alice").pk)
    )
    Delivery.objects.filter(notice=notice).update(state=DeliveryState.ABANDONED, attempt_count=5)
    pipeline.get_delivery_attempt_model().objects.create(
        delivery=mine,
        attempt_no=5,
        succeeded=False,
        status_code=404,
        error="The endpoint answered 404.",
        started_at=timezone.now(),
        duration_ms=12,
        triggered_by="send",
    )
    return mine


class TestDeliveries:
    def test_a_caller_sees_the_deliveries_to_their_endpoints_with_their_attempts(self, alice, hooked_delivery):
        listed = client_for(alice).get("/deliveries/").json()

        assert [each["id"] for each in listed] == [str(hooked_delivery.pk)]
        [delivery] = listed
        assert (delivery["kind"], delivery["variant"], delivery["channel"], delivery["state"]) == (
            "parcel_flagged",
            "lost",
            "webhook",
            "abandoned",
        )
        assert [(each["attempt_no"], each["status_code"]) for each in delivery["attempts"]] == [(5, 404)]
        assert client_for(alice).get(f"/deliveries/{hooked_delivery.pk}/").json()["id"] == str(hooked_delivery.pk)

    def test_retrying_an_abandoned_one(self, alice, hooked_delivery):
        answered = client_for(alice).post(f"/deliveries/{hooked_delivery.pk}/retry/", **keyed())

        assert answered.status_code == 200
        assert answered.json()["state"] == "pending"

    def test_a_retry_refused_says_why(self, alice, hooked_delivery):
        get_endpoint_model().objects.filter(url__startswith="https://alice").update(parked_at=timezone.now())

        answered = client_for(alice).post(f"/deliveries/{hooked_delivery.pk}/retry/", **keyed())

        assert (answered.status_code, answered.json()) == (400, {"detail": "The endpoint is parked - unpark it first."})

    def test_another_callers_delivery_isnt_found(self, bob, hooked_delivery):
        assert client_for(bob).post(f"/deliveries/{hooked_delivery.pk}/retry/", **keyed()).status_code == 404


class TestTheReviewsApiFixes:
    def test_a_change_to_an_endpoints_kinds_alone_moves_its_version(self, alice, endpoint):
        client = client_for(alice)
        before = version_of(client, endpoint)

        answered = client.patch(
            f"/endpoints/{endpoint.pk}/",
            {"kinds": [{"kind": "parcel_shipped"}]},
            format="json",
            **keyed(HTTP_IF_MATCH=before),
        )

        assert answered["ETag"] != before
        assert version_of(client, endpoint) == answered["ETag"]

    def test_a_subscription_raced_by_another_is_a_400(self, monkeypatch, alice):
        from django.db import IntegrityError

        def raced(**kwargs):
            raise IntegrityError("guard_variant")

        monkeypatch.setattr(get_subscription_model().objects, "update_or_create", raced)

        answered = client_for(alice).post("/subscriptions/", {"kind": "parcel_flagged"}, format="json")

        assert answered.json() == {
            "variant": [
                "alice@example.com's subscriptions to parcel_flagged were changed at the same time - try again."
            ]
        }


def test_the_owner_is_the_caller_by_default():
    from types import SimpleNamespace

    from isik.django.apps.notifications.drf import OwnedByCaller

    assert OwnedByCaller().owner(SimpleNamespace(user="alice")) == "alice"


class TestAListReadsEachRowsKindWithIt:
    def listed_with_the_kind(self, client, path, table):
        with CaptureQueriesContext(connection) as queries:
            assert client.get(path).status_code == 200
        return [
            query["sql"]
            for query in queries
            if f'FROM "{table}"' in query["sql"] and '"isik_notice_kind_policy"."retired_at"' in query["sql"]
        ]

    def test_the_inbox(self, alice):
        item(alice)

        assert self.listed_with_the_kind(client_for(alice), "/inbox/", "isik_inbox_item") != []

    def test_the_subscriptions(self, alice):
        subscribe(alice, "parcel_shipped", enabled=False)

        assert self.listed_with_the_kind(client_for(alice), "/subscriptions/", "isik_subscription") != []
