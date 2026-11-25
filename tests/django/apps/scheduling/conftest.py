import pytest
from django.db import connection

from isik.django import scheduling
from isik.django.apps.scheduling import state
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.conf import isik_with
from tests.testapp.models import EmailUser


CLEANUP = "testapp.cleanup"


@pytest.fixture(autouse=True)
def urls(settings, monkeypatch):
    settings.ROOT_URLCONF = "tests.django.apps.scheduling.urls"
    settings.ISIK = isik_with(DJANGO={"IDEMPOTENCY": {"CLAIM_MODEL": "idempotency_by_reference.IdempotencyClaim"}})
    monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", True)


@pytest.fixture
def changed(monkeypatch):
    """Changes a declared task for one test, as override_scheduled_task() would."""

    def change(name, **changes):
        monkeypatch.setitem(scheduling.overrides, name, {**scheduling.overrides.get(name, {}), **changes})

    return change


@pytest.fixture
def rows(db):
    """Every declared task's row, as migrate leaves them - by name."""
    get_task_state_model()._default_manager.all().delete()
    state.reconcile(restart=False)
    return {row.name: row for row in get_task_state_model()._default_manager.all()}


def row_of(name):
    return get_task_state_model()._default_manager.get(name=name)


@pytest.fixture
def staff(db):
    return EmailUser.objects.create(username="staff", email="staff@example.com", is_staff=True)
