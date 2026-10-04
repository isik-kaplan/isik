"""
IdempotencyMixin end to end: requests served through Django's own handler with ATOMIC_REQUESTS on,
so a claim lives and dies with its request's transaction exactly as it would in a project. What only
shows with two transactions at once - a retry waiting on the first - is in test_concurrency.py.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.viewsets import ModelViewSet

from isik.django.apps.idempotency.by_reference.models import IdempotencyClaim
from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.apps.idempotency.with_body.models import IdempotencyClaimWithBody
from tests.django.apps.idempotency.support import client_for, make_user, post
from tests.testapp.models import Widget


pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urls", "atomic_requests"),
]

REPLAYED = "Idempotent-Replayed"


@pytest.fixture
def alice():
    return make_user("alice")


@pytest.fixture
def client(alice):
    return client_for(alice)


def name_only(request):
    return request.data.get("name")


class TestTheFirstRequest:
    def test_does_the_work_and_says_nothing_of_replays(self, client, key):
        response = post(client, "/widgets/", {"name": "bolt"}, key)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"id": str(Widget.objects.get().pk), "name": "bolt", "count": 0}
        assert REPLAYED not in response

    def test_records_who_spent_the_key_on_what(self, client, alice, key):
        post(client, "/widgets/", {"name": "bolt"}, key)

        claim = IdempotencyClaim.objects.get()
        assert (claim.claimed_by, claim.key, claim.status, claim.object_id) == (
            alice,
            key,
            201,
            str(Widget.objects.get().pk),
        )
        assert len(claim.fingerprint) == 64

    def test_without_a_key_is_refused_before_any_work(self, client):
        response = post(client, "/widgets/", {"name": "bolt"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"Idempotency-Key": ["This header is required."]}
        assert response.data["Idempotency-Key"][0].code == "required"
        assert not Widget.objects.exists()

    @pytest.mark.parametrize("sent", ["not-a-uuid", ""])
    def test_with_a_key_that_isnt_a_uuid_is_refused(self, client, sent):
        response = client.post("/widgets/", {"name": "bolt"}, format="json", headers={"Idempotency-Key": sent})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"Idempotency-Key": ["Must be a UUID."]}
        assert response.data["Idempotency-Key"][0].code == "invalid"
        assert not Widget.objects.exists()

    def test_accepts_any_uuid_version(self, client):
        # A v7 key - time-ordered, and what more and more clients mint by default.
        response = post(client, "/widgets/", {"name": "bolt"}, "0192f5a4-7c3e-7b21-9a4d-5e6f7a8b9c0d")

        assert response.status_code == status.HTTP_201_CREATED

    def test_from_an_anonymous_caller_is_refused(self, key):
        response = post(APIClient(), "/body-widgets/", {"name": "bolt"}, key)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["detail"].code == "not_authenticated"
        assert not Widget.objects.exists()


class TestARetry:
    def test_answers_with_the_first_response_and_does_no_work(self, client, key):
        first = post(client, "/widgets/", {"name": "bolt"}, key)

        retry = post(client, "/widgets/", {"name": "bolt"}, key)

        assert (retry.status_code, retry.data) == (first.status_code, first.data)
        assert retry[REPLAYED] == "true"
        assert Widget.objects.count() == 1
        assert IdempotencyClaim.objects.count() == 1

    def test_with_its_json_reordered_is_still_a_retry(self, client, key):
        post(client, "/widgets/", {"name": "bolt", "count": 2}, key)

        retry = client.post(
            "/widgets/",
            '{"count": 2, "name": "bolt"}',
            content_type="application/json",
            headers={"Idempotency-Key": str(key)},
        )

        assert retry[REPLAYED] == "true"

    def test_by_another_caller_with_the_same_key_is_their_own_request(self, client, key):
        post(client, "/widgets/", {"name": "bolt"}, key)

        theirs = post(client_for(make_user("bob")), "/widgets/", {"name": "bolt"}, key)

        assert theirs.status_code == status.HTTP_201_CREATED
        assert REPLAYED not in theirs
        assert Widget.objects.count() == 2

    @pytest.mark.parametrize(
        ("url", "data"),
        [("/widgets/", {"name": "nut"}), ("/widgets/touch/", {"name": "bolt"})],
        ids=["another body", "another endpoint"],
    )
    def test_of_a_different_request_is_refused(self, client, key, url, data):
        post(client, "/widgets/", {"name": "bolt"}, key)

        response = post(client, url, data, key)

        assert response.status_code == 422
        assert response.data["detail"].code == "idempotency_key_reused"
        assert Widget.objects.count() == 1

    def test_of_a_detail_action_re_serializes_its_row(self, client, alice, key):
        widget = Widget.objects.create(name="bolt", owner=alice)
        post(client, f"/widgets/{widget.pk}/bump/", key=key)

        retry = post(client, f"/widgets/{widget.pk}/bump/", key=key)

        assert (retry.status_code, retry.json(), retry[REPLAYED]) == (
            200,
            {"id": str(widget.pk), "name": "bolt", "count": 1},
            "true",
        )
        widget.refresh_from_db()
        assert widget.count == 1

    def test_by_reference_answers_with_the_row_as_it_is_now(self, client, key):
        first = post(client, "/widgets/", {"name": "bolt"}, key)
        Widget.objects.filter(pk=first.data["id"]).update(name="renamed")

        retry = post(client, "/widgets/", {"name": "bolt"}, key)

        assert retry.json() == {"id": str(first.data["id"]), "name": "renamed", "count": 0}

    def test_by_reference_whose_row_is_gone_is_told_so(self, client, key):
        first = post(client, "/widgets/", {"name": "bolt"}, key)
        Widget.objects.filter(pk=first.data["id"]).delete()

        retry = post(client, "/widgets/", {"name": "bolt"}, key)

        assert retry.status_code == status.HTTP_410_GONE
        assert retry.data["detail"].code == "idempotent_replay_gone"

    def test_by_reference_checks_the_rows_permissions_again(self, client, key):
        first = post(client, "/widgets/", {"name": "bolt"}, key)
        Widget.objects.filter(pk=first.data["id"]).update(owner=make_user("bob"))

        retry = post(client, "/widgets/", {"name": "bolt"}, key)

        assert retry.status_code == status.HTTP_403_FORBIDDEN

    def test_of_a_response_without_a_body_answers_without_one(self, client, key):
        post(client, "/widgets/touch/", key=key)

        retry = post(client, "/widgets/touch/", key=key)

        assert (retry.status_code, retry.data, retry[REPLAYED]) == (204, None, "true")
        assert IdempotencyClaim.objects.get().object_id is None
        assert Widget.objects.count() == 1

    def test_with_a_kept_body_answers_with_the_body_as_it_was(self, client, key):
        first = post(client, "/body-widgets/", {"name": "bolt"}, key)
        Widget.objects.filter(pk=first.data["id"]).delete()

        retry = post(client, "/body-widgets/", {"name": "bolt"}, key)

        assert (retry.status_code, retry.json(), retry[REPLAYED]) == (201, first.json(), "true")
        claim = IdempotencyClaimWithBody.objects.get()
        assert claim.body == f'{{"id": "{first.data["id"]}", "name": "bolt", "count": 0}}'

    def test_with_a_kept_body_replays_any_response(self, client, key):
        first = post(client, "/body-widgets/plain_dict/", key=key)

        retry = post(client, "/body-widgets/plain_dict/", key=key)

        assert (retry.status_code, retry.json()) == (200, first.json())
        assert Widget.objects.count() == 1

    def test_on_a_view_that_isnt_a_viewset_works_the_same(self, client, key):
        first = post(client, "/create-widget/", {"name": "bolt"}, key)

        retry = post(client, "/create-widget/", {"name": "bolt"}, key)

        assert (retry.status_code, retry.data, retry[REPLAYED]) == (201, first.data, "true")
        assert Widget.objects.count() == 1


class TestAFailure:
    def test_that_was_raised_leaves_the_key_unspent(self, client, key):
        refused = post(client, "/widgets/", {"name": "bolt", "count": -1}, key)
        assert refused.status_code == status.HTTP_400_BAD_REQUEST

        retry = post(client, "/widgets/", {"name": "bolt", "count": -1}, key)

        # Refused for real again rather than replayed: the claim went with the rolled-back transaction.
        assert retry.status_code == status.HTTP_400_BAD_REQUEST
        assert REPLAYED not in retry
        assert not IdempotencyClaim.objects.exists()

    def test_that_was_raised_rolls_back_the_work_with_the_claim(self, client, key):
        assert post(client, "/widgets/explode/", key=key).status_code == status.HTTP_400_BAD_REQUEST

        assert not Widget.objects.exists()
        assert not IdempotencyClaim.objects.exists()

    def test_that_was_returned_releases_the_key_and_keeps_the_work(self, client, key):
        assert post(client, "/widgets/refuse/", key=key).status_code == status.HTTP_400_BAD_REQUEST
        assert not IdempotencyClaim.objects.exists()

        retry = post(client, "/widgets/refuse/", key=key)

        assert REPLAYED not in retry
        assert Widget.objects.filter(name="kept").count() == 2


class TestAnActionThatCantBeReplayed:
    @pytest.mark.parametrize("url", ["/widgets/issue_secret/", "/body-widgets/issue_secret/"])
    def test_answers_its_first_request_and_refuses_a_retry(self, client, key, url):
        first = post(client, url, key=key)
        assert first.status_code == status.HTTP_201_CREATED

        retry = post(client, url, key=key)

        assert retry.status_code == status.HTTP_409_CONFLICT
        assert retry.data["detail"].code == "idempotency_key_not_replayable"
        assert Widget.objects.count() == 1

    def test_keeps_only_its_status_never_its_body(self, client, key):
        post(client, "/body-widgets/issue_secret/", key=key)

        claim = IdempotencyClaimWithBody.objects.get()
        assert (claim.status, claim.body) == (201, None)

    def test_keeps_only_its_status_never_its_row(self, client, key):
        post(client, "/widgets/issue_secret/", key=key)

        claim = IdempotencyClaim.objects.get()
        assert (claim.status, claim.object_id) == (201, None)

    def test_with_a_different_request_is_still_told_its_key_was_reused(self, client, key):
        post(client, "/widgets/issue_secret/", key=key)

        assert post(client, "/widgets/issue_secret/", {"other": 1}, key).status_code == 422


class TestAResponseThatCantBeReplayedByReference:
    @pytest.mark.parametrize(
        ("action", "message_action"),
        [("plain_dict", "plain_dict"), ("other_serializer", "other_serializer"), ("not_a_row", "not_a_row")],
    )
    def test_is_refused_and_its_work_rolled_back(self, client, key, action, message_action):
        with pytest.raises(ImproperlyConfigured) as raised:
            post(client, f"/widgets/{action}/", key=key)

        assert str(raised.value) == (
            f"WidgetViewSet.{message_action} can't be replayed by reference: its response isn't its own "
            "serializer over one of its own rows. Declare it in idempotency_no_replay_actions, or keep response "
            "bodies with an AbstractIdempotencyClaimWithBody."
        )
        assert not Widget.objects.exists()
        assert not IdempotencyClaim.objects.exists()


class TestWhatIsCovered:
    def test_an_exempt_action_needs_no_key(self, client):
        response = post(client, "/widgets/preview/", {"name": "bolt"})

        assert response.status_code == status.HTTP_200_OK
        assert not IdempotencyClaim.objects.exists()

    def test_an_exempt_action_ignores_a_key_it_was_sent(self, client, key):
        post(client, "/widgets/preview/", {"name": "bolt"}, key)

        assert not IdempotencyClaim.objects.exists()

    def test_a_method_that_isnt_covered_needs_no_key(self, client, alice):
        widget = Widget.objects.create(name="bolt", owner=alice)

        response = client.patch(f"/widgets/{widget.pk}/", {"name": "nut"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert not IdempotencyClaim.objects.exists()

    def test_an_optional_key_left_out_is_an_ordinary_request(self, client):
        assert post(client, "/optional-widgets/", {"name": "bolt"}).status_code == status.HTTP_201_CREATED
        assert post(client, "/optional-widgets/", {"name": "bolt"}).status_code == status.HTTP_201_CREATED

        assert Widget.objects.count() == 2
        assert not IdempotencyClaim.objects.exists()

    def test_an_optional_key_sent_is_honored(self, client, key):
        post(client, "/optional-widgets/", {"name": "bolt"}, key)

        assert post(client, "/optional-widgets/", {"name": "bolt"}, key)[REPLAYED] == "true"
        assert Widget.objects.count() == 1


class TestTheNormalizer:
    def test_an_actions_own_decides_what_its_body_is(self, client, key):
        post(client, "/widgets/with_nonce/", {"name": "bolt", "nonce": 1}, key)

        retry = post(client, "/widgets/with_nonce/", {"name": "bolt", "nonce": 2}, key)

        assert retry[REPLAYED] == "true"

    def test_an_actions_own_is_its_alone(self, client, key):
        post(client, "/widgets/", {"name": "bolt", "nonce": 1}, key)

        assert post(client, "/widgets/", {"name": "bolt", "nonce": 2}, key).status_code == 422

    def test_one_set_on_the_class_is_a_plain_function_of_the_request(self, client, key):
        post(client, "/nonce-blind-widgets/", {"name": "bolt", "nonce": 1}, key)

        assert post(client, "/nonce-blind-widgets/", {"name": "bolt", "nonce": 2}, key)[REPLAYED] == "true"

    @override_settings(IDEMPOTENCY_NORMALIZE=name_only)
    def test_the_configured_one_applies_where_the_view_has_none(self, client, key):
        post(client, "/widgets/", {"name": "bolt", "count": 1}, key)

        assert post(client, "/widgets/", {"name": "bolt", "count": 2}, key)[REPLAYED] == "true"

    @override_settings(IDEMPOTENCY_NORMALIZE=name_only)
    def test_the_views_own_wins_over_the_configured_one(self, client, key):
        post(client, "/nonce-blind-widgets/", {"name": "bolt"}, key)

        assert post(client, "/nonce-blind-widgets/", {"name": "nut"}, key).status_code == 422


class TestTheHooksItOverrides:
    def test_hand_their_exact_arguments_on(self):
        # APIView.initial()/finalize_response() barely read their extra arguments, so a real base
        # can't tell a dropped one - a base that records what it was handed can.
        received = []

        class RecordingBase:
            def initial(self, request, *args, **kwargs):
                received.append(("initial", request, args, kwargs))

            def finalize_response(self, request, response, *args, **kwargs):
                received.append(("finalize_response", request, response, args, kwargs))
                return response

        class Recorded(IdempotencyMixin, RecordingBase):
            def is_idempotent(self, request):
                return False

        request, response = object(), object()
        view = Recorded()
        view.initial(request, "extra", pk="1")
        assert view.finalize_response(request, response, "extra", pk="1") is response

        assert received == [
            ("initial", request, ("extra",), {"pk": "1"}),
            ("finalize_response", request, response, ("extra",), {"pk": "1"}),
        ]


class TestDeclaringExemptions:
    @pytest.mark.parametrize("attribute", ["idempotency_exempt_actions", "idempotency_no_replay_actions"])
    @pytest.mark.parametrize("reason", ["", "   ", None])
    def test_an_action_needs_a_reason(self, attribute, reason):
        with pytest.raises(ImproperlyConfigured) as raised:
            type("ReasonlessViewSet", (IdempotencyMixin, ModelViewSet), {attribute: {"preview": reason}})

        assert str(raised.value) == f"ReasonlessViewSet.{attribute} needs a reason for preview, not {reason!r}."

    def test_a_reason_is_all_it_needs(self):
        declared = type(
            "ReasonedViewSet",
            (IdempotencyMixin, ModelViewSet),
            {"idempotency_exempt_actions": {"preview": "changes nothing"}, "idempotency_no_replay_actions": {"x": "y"}},
        )

        assert declared.idempotency_exempt_actions == {"preview": "changes nothing"}

    def test_a_subclass_keeps_its_parents_init_subclass(self):
        seen = []

        class Recording:
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
                seen.append(cls.__name__)

        type("Recorded", (IdempotencyMixin, Recording), {})

        assert seen == ["Recorded"]
