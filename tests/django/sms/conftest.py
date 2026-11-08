import httpx
import pytest

from isik.django.conf import isik_areas


@pytest.fixture
def senders_configured(settings):
    """Sets ISIK['DJANGO']['SMS'] to `config` - the senders as given, not made locmem."""

    def configure(config):
        isik_areas(settings).SMS = config

    return configure


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A request that isn't through a test's own transport fails - no SMS test reaches the network."""

    def refuse(self, request):
        raise AssertionError(f"An SMS test reached for the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
