import hashlib
import hmac
import json
import time

import pytest
from django.test import Client, TestCase

from tests.testapp import inbound_sources


@pytest.fixture(autouse=True)
def urls(settings):
    settings.ROOT_URLCONF = "tests.django.apps.inbound.urls"


@pytest.fixture(autouse=True)
def handled():
    inbound_sources.HANDLED.clear()
    yield inbound_sources.HANDLED
    inbound_sources.HANDLED.clear()


@pytest.fixture
def asked(monkeypatch):
    """The process tasks the pipeline asked for, in order, instead of a broker: [(partition, event id)]."""
    from isik.django.apps.inbound import tasks

    asked = []
    monkeypatch.setattr(tasks.process, "delay", lambda partition, event_id: asked.append((partition, event_id)))
    return asked


def signed(body, secret=inbound_sources.SECRET, stamp=None):
    stamp = int(time.time()) if stamp is None else stamp
    signature = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={signature}"


def post(source, payload, header="X-Acme-Signature", signature=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {} if signature is False else {header: signature or signed(body)}
    # What commits with the request runs, as it would once a real request's transaction commits.
    with TestCase.captureOnCommitCallbacks(execute=True):
        return Client().post(f"/in/{source}/", body, content_type="application/json", headers=headers)
