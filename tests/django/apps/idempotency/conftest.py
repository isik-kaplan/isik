import uuid

import pytest
from django.db import connection


@pytest.fixture
def atomic_requests(monkeypatch):
    """ATOMIC_REQUESTS on - read per request by Django's handler, so turning it on here is enough."""
    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", True)


@pytest.fixture
def key():
    return uuid.uuid4()


@pytest.fixture
def urls(settings):
    """Serves the views in views.py - the test settings have no ROOT_URLCONF of their own to point elsewhere."""
    settings.ROOT_URLCONF = "tests.django.apps.idempotency.urls"
