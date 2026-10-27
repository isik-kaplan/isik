import pytest
from django.db import connection


@pytest.fixture(autouse=True)
def atomic_requests(monkeypatch):
    """ATOMIC_REQUESTS on - read per request by Django's handler, so turning it on here is enough."""
    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", True)


@pytest.fixture(autouse=True)
def urls(settings):
    settings.ROOT_URLCONF = "tests.django.apps.feedback.api.urls"
