import pytest
from django.db import connection

from isik.django.conf import isik_with


@pytest.fixture(autouse=True)
def atomic_requests(monkeypatch):
    """ATOMIC_REQUESTS on - read per request by Django's handler, so turning it on here is enough."""
    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", True)


@pytest.fixture(autouse=True)
def urls(settings):
    settings.ROOT_URLCONF = "tests.django.apps.notifications.api.urls"
    # The test project installs both claim apps; a project installs one.
    settings.ISIK = isik_with(DJANGO={"IDEMPOTENCY": {"CLAIM_MODEL": "idempotency_by_reference.IdempotencyClaim"}})
