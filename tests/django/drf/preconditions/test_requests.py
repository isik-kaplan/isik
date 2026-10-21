"""
PreconditionMixin end to end: requests served through Django's own handler with ATOMIC_REQUESTS on.
What only shows with two transactions at once - the lock - is in test_races.py.
"""

import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers, status
from rest_framework.test import APIClient

from isik.django.apps.common.db.concurrency import current_concurrency_etag
from isik.django.drf.viewsets import PreconditionMixin
from tests.django.apps.idempotency.support import client_for, make_user
from tests.testapp.models import VersionedWidget, Widget


pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urls", "atomic_requests"),
]

REQUIRED = {
    "detail": "This request must say which version it was made against, in an If-Match header.",
    "code": "precondition_required",
}


@pytest.fixture
def widget(alice):
    return Widget.objects.create(name="bolt", owner=alice)


def etag(widget):
    return current_concurrency_etag(type(widget), widget.pk)


def retrieved(client, widget):
    return client.get(f"/widgets/{widget.pk}/").json()


def write(client, method, url, data=None, if_match=None):
    headers = {} if if_match is None else {"If-Match": if_match}
    return getattr(client, method)(url, data, format="json", headers=headers)


class TestReading:
    def test_retrieve_sends_the_rows_version_as_a_strong_etag(self, client, widget):
        response = client.get(f"/widgets/{widget.pk}/")

        assert response.status_code == status.HTTP_200_OK
        assert response["ETag"] == etag(widget)
        assert response["ETag"].startswith('"')
        # Spelled as RFC 9110 spells it, though a header's name is matched without regard to case.
        assert "ETag" in dict(response.items())
        assert response.json()["etag"] == response["ETag"]

    @pytest.mark.parametrize("sent", ["{}", "W/{}", '"stale", {}', "*"])
    def test_if_none_match_naming_the_current_version_is_a_304(self, client, widget, sent):
        current = etag(widget)

        response = client.get(f"/widgets/{widget.pk}/", headers={"If-None-Match": sent.format(current)})

        assert response.status_code == status.HTTP_304_NOT_MODIFIED
        assert response.content == b""
        assert response["ETag"] == current
        assert "ETag" in dict(response.items())

    def test_if_none_match_naming_an_older_version_is_answered_in_full(self, client, widget):
        stale = etag(widget)
        Widget.objects.filter(pk=widget.pk).update(name="nut")

        response = client.get(f"/widgets/{widget.pk}/", headers={"If-None-Match": stale})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "nut"
        assert response["ETag"] == etag(widget) != stale

    def test_a_list_sends_no_etag_and_each_item_carries_its_own(self, client, widget):
        other = Widget.objects.create(name="nut", owner=widget.owner)

        response = client.get("/widgets/")

        assert "ETag" not in response
        assert {item["id"]: item["etag"] for item in response.json()} == {
            str(widget.pk): etag(widget),
            str(other.pk): etag(other),
        }

    def test_another_detail_read_sends_none(self, client, widget):
        response = client.get(f"/widgets/{widget.pk}/peek/", headers={"If-None-Match": etag(widget)})

        assert response.status_code == status.HTTP_200_OK
        assert "ETag" not in response

    def test_a_view_that_isnt_a_viewset_sends_one_on_its_get(self, client, widget):
        response = client.get(f"/widget/{widget.pk}/")

        assert response["ETag"] == etag(widget)

    def test_a_version_is_its_number(self, client):
        versioned = VersionedWidget.objects.create(name="bolt")

        assert client.get(f"/versioned-widgets/{versioned.pk}/")["ETag"] == '"1"'


class TestWritingWithoutIfMatch:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("put", ""), ("patch", ""), ("delete", ""), ("post", "bump/")],
    )
    def test_is_refused_with_428_and_changes_nothing(self, client, widget, method, path):
        before = etag(widget)

        response = write(client, method, f"/widgets/{widget.pk}/{path}", {"name": "nut", "count": 1})

        assert response.status_code == status.HTTP_428_PRECONDITION_REQUIRED
        assert response.json() == REQUIRED
        assert "ETag" not in response
        assert etag(widget) == before
        assert Widget.objects.get().name == "bolt"

    def test_on_a_view_that_isnt_a_viewset_too(self, client, widget):
        response = write(client, "put", f"/widget/{widget.pk}/", {"name": "nut"})

        assert response.status_code == status.HTTP_428_PRECONDITION_REQUIRED

    def test_rolls_back_what_the_request_did_before_the_check(self, client, widget):
        response = write(client, "post", f"/widgets/{widget.pk}/write_then_check/")

        assert response.status_code == status.HTTP_428_PRECONDITION_REQUIRED
        assert list(Widget.objects.values_list("name", flat=True)) == ["bolt"]

    def test_is_refused_only_after_the_404(self, client):
        response = write(client, "patch", f"/widgets/{uuid.uuid4()}/", {"name": "nut"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_is_refused_only_after_the_permissions(self, widget):
        response = write(client_for(make_user("bob")), "patch", f"/widgets/{widget.pk}/", {"name": "nut"})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_is_refused_only_after_authentication(self, widget):
        response = write(APIClient(), "patch", f"/widgets/{widget.pk}/", {"name": "nut"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"] == "Authentication credentials were not provided."

    def test_an_exempt_action_doesnt_check_one_it_is_sent(self, client, widget):
        response = write(client, "post", f"/widgets/{widget.pk}/rename_freely/", {"name": "nut"}, '"stale"')

        assert response.status_code == status.HTTP_200_OK

    def test_an_exempt_action_needs_none(self, client, widget):
        response = write(client, "post", f"/widgets/{widget.pk}/rename_freely/", {"name": "nut"})

        assert response.status_code == status.HTTP_200_OK
        assert Widget.objects.get().name == "nut"
        assert response["ETag"] == etag(widget)

    def test_a_create_needs_none_and_sends_its_new_rows(self, client):
        response = write(client, "post", "/widgets/", {"name": "bolt"})

        assert response.status_code == status.HTTP_201_CREATED
        assert response["ETag"] == etag(Widget.objects.get()) == response.json()["etag"]

    def test_a_create_on_a_view_that_isnt_a_viewset_sends_its_new_rows(self, client):
        response = write(client, "post", "/widget/", {"name": "bolt"})

        assert response.status_code == status.HTTP_201_CREATED
        assert response["ETag"] == etag(Widget.objects.get())

    def test_a_refused_create_sends_none(self, client):
        response = write(client, "post", "/widgets/", {"name": ""})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "ETag" not in response

    def test_a_list_on_a_view_that_isnt_a_viewset_sends_none(self, client, widget):
        assert "ETag" not in client.get("/widget/")

    def test_a_post_that_makes_no_serializer_sends_none(self, client):
        response = write(client, "post", "/ping/")

        assert response.status_code == status.HTTP_200_OK
        assert "ETag" not in response

    def test_a_post_to_no_one_object_needs_none_and_sends_none(self, client):
        response = write(client, "post", "/widgets/make_two/")

        assert response.status_code == status.HTTP_200_OK
        assert "ETag" not in response


class TestWritingAgainstTheCurrentVersion:
    @pytest.mark.parametrize("sent", ["{}", "W/{}", '"stale", {}', '"stale",W/{}', " {} ", "*"])
    def test_succeeds(self, client, widget, sent):
        response = write(client, "patch", f"/widgets/{widget.pk}/", {"name": "nut"}, sent.format(etag(widget)))

        assert response.status_code == status.HTTP_200_OK
        assert Widget.objects.get().name == "nut"

    def test_sends_the_version_the_write_left_in_the_header_and_the_body(self, client, widget):
        before = etag(widget)

        response = write(client, "patch", f"/widgets/{widget.pk}/", {"name": "nut"}, before)

        assert response["ETag"] == etag(widget) != before
        assert "ETag" in dict(response.items())
        assert response.json()["etag"] == response["ETag"]

    def test_a_put_and_an_action_send_the_new_version_too(self, client, widget):
        put = write(client, "put", f"/widgets/{widget.pk}/", {"name": "nut", "count": 2}, etag(widget))
        assert put.status_code == status.HTTP_200_OK
        assert put["ETag"] == etag(widget) == put.json()["etag"]

        bumped = write(client, "post", f"/widgets/{widget.pk}/bump/", if_match=put["ETag"])
        assert bumped.status_code == status.HTTP_200_OK
        assert bumped["ETag"] == etag(widget) != put["ETag"]
        assert Widget.objects.get().count == 3

    def test_a_delete_sends_no_version_since_there_is_none(self, client, widget):
        response = write(client, "delete", f"/widgets/{widget.pk}/", if_match=etag(widget))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert "ETag" not in response
        assert not Widget.objects.exists()

    def test_the_tag_it_sent_back_is_the_next_writes(self, client, widget):
        first = write(client, "patch", f"/widgets/{widget.pk}/", {"name": "nut"}, etag(widget))
        second = write(client, "patch", f"/widgets/{widget.pk}/", {"name": "washer"}, first["ETag"])

        assert second.status_code == status.HTTP_200_OK
        assert Widget.objects.get().name == "washer"

    def test_a_versioned_rows_version_is_one_more(self, client):
        versioned = VersionedWidget.objects.create(name="bolt")

        response = write(client, "patch", f"/versioned-widgets/{versioned.pk}/", {"name": "nut"}, '"1"')

        assert response["ETag"] == '"2"'
        assert response.json()["version"] == 2
        # Everything the triggers set is read back, not the token alone.
        versioned.refresh_from_db()
        assert response.json()["updated_at"] == serializers.DateTimeField().to_representation(versioned.updated_at)
        assert response.json()["etag"] == '"2"'

    def test_on_a_view_that_isnt_a_viewset(self, client, widget):
        response = write(client, "patch", f"/widget/{widget.pk}/", {"name": "nut"}, etag(widget))

        assert response.status_code == status.HTTP_200_OK
        assert response["ETag"] == etag(widget)


class TestWritingAgainstAnOlderVersion:
    def test_is_refused_with_412_showing_the_row_as_it_is_now(self, client, widget):
        stale = etag(widget)
        Widget.objects.filter(pk=widget.pk).update(name="nut")

        response = write(client, "patch", f"/widgets/{widget.pk}/", {"name": "washer"}, stale)

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert response.json() == {
            "detail": "This changed since the version the request was made against.",
            "code": "precondition_failed",
            # As retrieve shows it, not as the write would have answered.
            "current": retrieved(client, widget),
        }
        assert response["ETag"] == etag(widget) == response.json()["current"]["etag"]
        assert "ETag" in dict(response.items())
        assert Widget.objects.get().name == "nut"

    @pytest.mark.parametrize("sent", ['"stale"', 'W/"stale"', '"stale", "older"', "", "W/"])
    def test_any_tag_but_the_current_one_is_refused(self, client, widget, sent):
        response = write(client, "patch", f"/widgets/{widget.pk}/", {"name": "nut"}, sent)

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED

    def test_a_tag_made_from_the_rendered_updated_at_is_refused(self, client, widget):
        rendered = retrieved(client, widget)["updated_at"]

        response = write(client, "patch", f"/widgets/{widget.pk}/", {"name": "nut"}, f'"{rendered}"')

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED

    def test_a_write_landing_between_the_read_and_the_lock_is_caught(self, client, widget):
        before = etag(widget)

        response = write(client, "patch", f"/underfoot-widgets/{widget.pk}/", {"name": "nut"}, before)

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert response.json()["current"]["name"] == "changed underfoot"

    def test_rolls_back_what_the_request_did_before_the_check(self, client, widget):
        response = write(client, "post", f"/widgets/{widget.pk}/write_then_check/", if_match='"stale"')

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert list(Widget.objects.values_list("name", flat=True)) == ["bolt"]

    def test_a_delete_is_refused_too(self, client, widget):
        response = write(client, "delete", f"/widgets/{widget.pk}/", if_match='"stale"')

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert Widget.objects.exists()

    def test_a_versioned_row_compares_its_version(self, client):
        versioned = VersionedWidget.objects.create(name="bolt")
        VersionedWidget.objects.filter(pk=versioned.pk).update(name="nut")

        response = write(client, "patch", f"/versioned-widgets/{versioned.pk}/", {"name": "washer"}, '"1"')

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert response["ETag"] == '"2"'


class TestIfMatchAny:
    def test_a_row_that_isnt_there_is_still_a_404(self, client):
        response = write(client, "patch", f"/widgets/{uuid.uuid4()}/", {"name": "nut"}, "*")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db(transaction=True)
def test_without_a_transaction_the_lock_cant_be_held(settings, monkeypatch, alice):
    from django.db import connection

    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", False)
    settings.ROOT_URLCONF = "tests.django.drf.preconditions.urls"
    widget = Widget.objects.create(name="bolt", owner=alice)

    with pytest.raises(ImproperlyConfigured) as raised:
        write(client_for(alice), "patch", f"/widgets/{widget.pk}/", {"name": "nut"}, etag(widget))

    assert str(raised.value) == (
        "WidgetViewSet compares If-Match with the row it locks, which needs its request served in a "
        "transaction - turn on ATOMIC_REQUESTS."
    )


class TestUnderIdempotencyKeys:
    """A retry replays its first success instead of finding the version it named has moved on - which
    its own first attempt is what moved."""

    @pytest.mark.parametrize("route", ["idempotent-first-widgets", "precondition-first-widgets"])
    def test_a_retry_after_the_row_moved_on_replays_its_first_success(self, client, widget, route):
        key, before = str(uuid.uuid4()), etag(widget)
        headers = {"If-Match": before, "Idempotency-Key": key}

        first = client.patch(f"/{route}/{widget.pk}/", {"name": "nut"}, format="json", headers=headers)
        retry = client.patch(f"/{route}/{widget.pk}/", {"name": "nut"}, format="json", headers=headers)

        assert first.status_code == retry.status_code == status.HTTP_200_OK
        assert retry["Idempotent-Replayed"] == "true"
        assert retry.json()["name"] == "nut"

    @pytest.mark.parametrize("route", ["idempotent-first-widgets", "precondition-first-widgets"])
    def test_a_refused_write_doesnt_spend_the_key(self, client, widget, route):
        key = str(uuid.uuid4())
        stale = {"If-Match": '"stale"', "Idempotency-Key": key}
        current = {"If-Match": etag(widget), "Idempotency-Key": key}

        refused = client.patch(f"/{route}/{widget.pk}/", {"name": "nut"}, format="json", headers=stale)
        accepted = client.patch(f"/{route}/{widget.pk}/", {"name": "nut"}, format="json", headers=current)

        assert refused.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert accepted.status_code == status.HTTP_200_OK
        assert "Idempotent-Replayed" not in accepted


class TestTheHooksItOverrides:
    def test_finalize_response_hands_its_exact_arguments_on(self):
        # APIView.finalize_response() doesn't read its extra arguments, so a real base can't tell a
        # dropped one - a base that records what it was handed can.
        received = []

        class RecordingBase:
            def finalize_response(self, request, response, *args, **kwargs):
                received.append((request, response, args, kwargs))
                return response

        class Recorded(PreconditionMixin, RecordingBase):
            pass

        request, response = type("Request", (), {"method": "GET"})(), object()

        assert Recorded().finalize_response(request, response, "extra", pk="1") is response
        assert received == [(request, response, ("extra",), {"pk": "1"})]
