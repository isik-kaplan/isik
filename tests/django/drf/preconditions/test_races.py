"""
What only shows with two transactions at once: a write arriving while another write to the same row
is still being served. Each request runs on its own thread, and so its own connection and
transaction - the first is held inside its handler (`views.gate`), holding the row's lock, until the
second is seen waiting on it in Postgres.
"""

import threading

import pytest
from django.db import connections
from rest_framework import status

from isik.django.apps.common.db.concurrency import current_concurrency_etag
from tests.django.apps.idempotency.support import client_for
from tests.django.apps.idempotency.test_concurrency import wait_until_someone_waits_on_a_lock
from tests.django.drf.preconditions import views
from tests.testapp.models import Widget


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("urls", "atomic_requests")]


@pytest.fixture
def gate(monkeypatch):
    gate = views.Gate()
    monkeypatch.setattr(views, "gate", gate)
    yield gate
    gate.released.set()


class Request(threading.Thread):
    """Serves one write on its own thread - see the idempotency tests' own for why errors come back as 500s."""

    def __init__(self, user, path, if_match):
        super().__init__()
        self.user, self.path, self.if_match = user, path, if_match
        self.response = None

    def run(self):
        try:
            client = client_for(self.user, raise_request_exception=False)
            self.response = client.post(self.path, format="json", headers={"If-Match": self.if_match})
        finally:
            connections.close_all()


def test_two_writes_made_against_one_version_land_one_and_refuse_the_other(gate, alice):
    widget = Widget.objects.create(name="bolt", owner=alice)
    read = current_concurrency_etag(Widget, widget.pk)
    first = Request(alice, f"/widgets/{widget.pk}/slow_bump/", read)
    second = Request(alice, f"/widgets/{widget.pk}/bump/", read)

    first.start()
    assert gate.entered.wait(10)
    second.start()
    # The second waits on the first's lock rather than comparing against the version it holds.
    wait_until_someone_waits_on_a_lock()
    gate.released.set()
    first.join(10)
    second.join(10)

    assert first.response.status_code == status.HTTP_200_OK
    assert second.response.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert second.response.json()["current"]["count"] == 1
    assert Widget.objects.get().count == 1
