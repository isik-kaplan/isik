"""A 412's `current` is shown only to a caller who may read the row - never through a write's gates alone."""

import uuid

import pytest
from rest_framework import status

from isik.django.apps.common.db.concurrency import current_concurrency_etag
from tests.django.apps.idempotency.support import client_for, make_user
from tests.testapp.models import Widget


pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("gates", "atomic_requests")]


@pytest.fixture
def gates(settings):
    settings.ROOT_URLCONF = "tests.django.drf.preconditions.gate_urls"


def stale_patch(client, path):
    return client.patch(path, {"name": "nut"}, format="json", headers={"If-Match": '"stale"'})


def assert_shown_nothing(response):
    assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert response.json()["current"] is None
    assert "ETag" not in response


@pytest.mark.parametrize(
    "path",
    [
        "write-not-read",  # a permission per action
        "narrower-reads",  # a queryset per action
        "object-read-refused",  # an object permission per action
    ],
)
def test_a_caller_who_may_write_but_not_read_gets_no_current(client, path):
    widget = Widget.objects.create(name="hidden")

    assert_shown_nothing(stale_patch(client, f"/{path}/{widget.pk}/"))


def test_a_caller_who_may_read_gets_it(client):
    widget = Widget.objects.create(name="visible")

    response = stale_patch(client, f"/narrower-reads/{widget.pk}/")

    assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert response.json()["current"]["name"] == "visible"
    assert response["ETag"] == current_concurrency_etag(Widget, widget.pk)


def test_current_hides_what_the_callers_read_hides():
    widget = Widget.objects.create(name="bolt", count=3)
    path = f"/field-hiding/{widget.pk}/"

    plain = stale_patch(client_for(make_user("plain")), path)
    staff_user = make_user("staff")
    staff_user.is_staff = True
    staff_user.save()
    staff = stale_patch(client_for(staff_user), path)

    assert "count" not in plain.json()["current"]
    assert staff.json()["current"]["count"] == 3


def test_an_object_refused_to_the_caller_is_a_403_before_any_412(client):
    widget = Widget.objects.create(name="bolt")

    assert stale_patch(client, f"/object-refused/{widget.pk}/").status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(("missing_row_status", "expected"), [(404, 404), (412, 412)])
def test_a_row_outside_the_callers_queryset_shows_nothing(client, settings, missing_row_status, expected):
    settings.PRECONDITION_MISSING_ROW_STATUS = missing_row_status
    settings.ROOT_URLCONF = "tests.django.drf.preconditions.urls"

    response = stale_patch(client, f"/widgets/{uuid.uuid4()}/")

    assert response.status_code == expected
    if expected == 412:
        assert_shown_nothing(response)


@pytest.mark.parametrize(("missing_row_status", "expected"), [(404, 404), (412, 412)])
@pytest.mark.parametrize("sent", ['"stale"', "*"])
def test_another_callers_row_is_no_more_than_a_missing_one(client, settings, missing_row_status, expected, sent):
    settings.PRECONDITION_MISSING_ROW_STATUS = missing_row_status
    theirs = Widget.objects.create(name="theirs", owner=make_user("bob"))

    response = client.patch(f"/own-rows/{theirs.pk}/", {"name": "nut"}, format="json", headers={"If-Match": sent})

    assert response.status_code == expected
    if expected == 412:
        assert_shown_nothing(response)
    assert Widget.objects.get().name == "theirs"
