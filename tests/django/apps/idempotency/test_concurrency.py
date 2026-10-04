"""
What only shows with two transactions at once: a retry arriving while the first request is still
being served. Each request runs on its own thread, and so its own connection and transaction - the
first is held inside its handler (`views.gate`) until the retry is seen waiting on it in Postgres.
"""

import threading
import time

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, connections
from rest_framework import status

from tests.django.apps.idempotency import views
from tests.django.apps.idempotency.support import client_for, make_user, post
from tests.testapp.models import Widget


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("urls")]

REPLAYED = "Idempotent-Replayed"


@pytest.fixture
def gate(monkeypatch):
    gate = views.Gate()
    monkeypatch.setattr(views, "gate", gate)
    yield gate
    gate.released.set()


@pytest.fixture
def alice():
    return make_user("alice")


class Request(threading.Thread):
    """
    Serves one request on its own thread. A crash comes back as the 500 a caller would see: the test
    client hears of every request's exception through one global signal, so re-raising it would
    re-raise one thread's crash on another.
    """

    def __init__(self, user, key, data):
        super().__init__()
        self.user, self.key, self.data = user, key, data
        self.response = None

    def run(self):
        try:
            client = client_for(self.user, raise_request_exception=False)
            self.response = post(client, "/widgets/slow/", self.data, self.key)
        finally:
            connections.close_all()


def wait_until_someone_waits_on_a_lock():
    with connection.cursor() as cursor:
        for _ in range(500):
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()"
            )
            if cursor.fetchone()[0]:
                return
            time.sleep(0.01)
    raise AssertionError("nobody waited on a lock")


def first_then_retry(gate, alice, key):
    first, retry = Request(alice, key, {"name": "bolt"}), Request(alice, key, {"name": "bolt"})
    first.start()
    assert gate.entered.wait(10)
    retry.start()
    return first, retry


@pytest.mark.usefixtures("atomic_requests")
def test_a_retry_waits_for_the_first_request_and_replays_it(gate, alice, key):
    first, retry = first_then_retry(gate, alice, key)
    wait_until_someone_waits_on_a_lock()

    gate.released.set()
    first.join(10)
    retry.join(10)

    assert first.response.status_code == status.HTTP_201_CREATED
    assert (retry.response.status_code, retry.response.json(), retry.response[REPLAYED]) == (
        201,
        first.response.json(),
        "true",
    )
    assert Widget.objects.count() == 1


@pytest.mark.usefixtures("atomic_requests")
def test_a_retry_after_a_crash_does_the_work_for_real(gate, alice, key):
    gate.explode = True
    first, retry = first_then_retry(gate, alice, key)
    wait_until_someone_waits_on_a_lock()

    gate.released.set()
    first.join(10)
    retry.join(10)

    # The crash rolled the claim back with everything else - nothing to reap, no claim left stuck.
    assert first.response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert retry.response.status_code == status.HTTP_201_CREATED
    assert REPLAYED not in retry.response
    assert list(Widget.objects.values_list("name", flat=True)) == ["bolt"]


@pytest.mark.usefixtures("atomic_requests")
def test_a_retry_that_runs_out_of_lock_timeout_is_told_to_retry_later(gate, alice, key, settings):
    settings.IDEMPOTENCY_LOCK_TIMEOUT = 50
    first, retry = first_then_retry(gate, alice, key)

    retry.join(10)
    gate.released.set()
    first.join(10)

    assert retry.response.status_code == status.HTTP_409_CONFLICT
    assert retry.response.json()["detail"] == (
        "A request with this idempotency key is still being served. Retry it shortly."
    )
    assert first.response.status_code == status.HTTP_201_CREATED
    assert Widget.objects.count() == 1


def test_a_request_outside_a_transaction_is_a_misconfiguration(alice, key):
    with pytest.raises(ImproperlyConfigured) as raised:
        post(client_for(alice), "/widgets/", {"name": "bolt"}, key)

    assert str(raised.value) == (
        "WidgetViewSet honors idempotency keys, which needs its request served in a transaction - "
        "turn on ATOMIC_REQUESTS."
    )
    assert not Widget.objects.exists()
