"""
Claims routed to a database of their own. Every query about a claim - the transaction check, the
savepoint, the insert, the read-back, the rollback check - has to follow the router to that database,
and the request's other work stays where it was.

The suite has one database, so the claims' database is the same one through a second connection,
registered here rather than in settings: its own settings dict and its own transaction, which is all
routing needs to be observable. Reads of a claim are routed nowhere, so one that doesn't stay on the
claims' write connection fails outright instead of happening to see the same rows.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from rest_framework import status

from isik.django.apps.idempotency.by_reference.models import IdempotencyClaim
from tests.django.apps.idempotency.support import client_for, make_user, post
from tests.testapp.models import Widget


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("urls", "claims_database")]

REPLAYED = "Idempotent-Replayed"


def is_claim(model):
    return model._meta.app_label.startswith("idempotency_")


class ClaimsRouter:
    def db_for_write(self, model, **hints):
        return "claims" if is_claim(model) else None

    def db_for_read(self, model, **hints):
        return "nowhere" if is_claim(model) else None

    def allow_relation(self, obj1, obj2, **hints):
        # One physical database underneath, so a claim may point at a user on the other connection.
        return True


@pytest.fixture
def claims_database(settings):
    settings.DATABASE_ROUTERS = ["tests.django.apps.idempotency.test_routing.ClaimsRouter"]
    connections.settings["claims"] = {**connections["default"].settings_dict, "ATOMIC_REQUESTS": False}
    # Opened here directly: the test case only lets a connection it wasn't told about be used, never opened.
    connections["claims"].connect()
    yield
    connections["claims"].close()
    del connections["claims"]
    del connections.settings["claims"]


def atomic_requests_on(*aliases):
    for alias in aliases:
        connections.settings[alias]["ATOMIC_REQUESTS"] = True


@pytest.fixture
def alice():
    return make_user("alice")


@pytest.fixture
def default_atomic_requests(monkeypatch):
    monkeypatch.setitem(connections["default"].settings_dict, "ATOMIC_REQUESTS", True)


@pytest.mark.usefixtures("default_atomic_requests")
def test_a_claim_is_made_and_replayed_on_the_claims_database(alice, key):
    atomic_requests_on("claims")
    first = post(client_for(alice), "/widgets/", {"name": "bolt"}, key)

    retry = post(client_for(alice), "/widgets/", {"name": "bolt"}, key)

    assert (retry.status_code, retry.json(), retry[REPLAYED]) == (201, first.json(), "true")
    assert IdempotencyClaim.objects.using("claims").get().key == key
    assert Widget.objects.count() == 1


@pytest.mark.usefixtures("default_atomic_requests")
def test_its_the_claims_database_that_must_be_in_a_transaction(alice, key):
    with pytest.raises(ImproperlyConfigured):
        post(client_for(alice), "/widgets/", {"name": "bolt"}, key)


def test_a_raised_failure_is_rolled_back_on_the_claims_database(alice, key):
    # Only the claims' database serves requests in a transaction here - so asking any other whether
    # it's rolling back would raise rather than answer.
    atomic_requests_on("claims")

    response = post(client_for(alice), "/widgets/explode/", key=key)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not IdempotencyClaim.objects.using("claims").exists()


def test_a_returned_failure_releases_its_claim_on_the_claims_database(alice, key):
    atomic_requests_on("claims")

    response = post(client_for(alice), "/widgets/refuse/", key=key)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not IdempotencyClaim.objects.using("claims").exists()
