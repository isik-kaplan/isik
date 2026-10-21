"""
Writes of the rows routed to a database of their own. The lock, the check that it can be held, and
the read-back of the version the write left all follow the router to it - the write is there, in a
transaction no other connection sees into until it commits.

The suite has one database, so the rows' database is the same one through a second connection - see
the idempotency routing tests, which this follows.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from isik.django.apps.common.db.concurrency import current_concurrency_etag
from tests.django.apps.idempotency.support import client_for
from tests.testapp.models import Widget


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("urls", "rows_database")]


class RowsRouter:
    def db_for_write(self, model, **hints):
        return "rows" if model is Widget else None

    def allow_relation(self, obj1, obj2, **hints):
        return True


@pytest.fixture
def rows_database(settings):
    settings.DATABASE_ROUTERS = ["tests.django.drf.preconditions.test_routing.RowsRouter"]
    connections.settings["rows"] = {**connections["default"].settings_dict, "ATOMIC_REQUESTS": True}
    connections["rows"].connect()
    yield
    connections["rows"].close()
    del connections["rows"]
    del connections.settings["rows"]


def test_the_lock_and_the_read_back_are_on_the_rows_database(alice):
    widget = Widget.objects.create(name="bolt", owner=alice)
    before = current_concurrency_etag(Widget, widget.pk)

    response = client_for(alice).patch(
        f"/widgets/{widget.pk}/", {"name": "nut"}, format="json", headers={"If-Match": before}
    )

    assert response.status_code == 200
    assert response["ETag"] == current_concurrency_etag(Widget, widget.pk) != before


def test_its_the_rows_database_that_must_be_in_a_transaction(alice, monkeypatch):
    monkeypatch.setitem(connections["default"].settings_dict, "ATOMIC_REQUESTS", True)
    connections.settings["rows"]["ATOMIC_REQUESTS"] = False
    widget = Widget.objects.create(name="bolt", owner=alice)

    with pytest.raises(ImproperlyConfigured):
        client_for(alice).patch(
            f"/widgets/{widget.pk}/",
            {"name": "nut"},
            format="json",
            headers={"If-Match": current_concurrency_etag(Widget, widget.pk)},
        )
