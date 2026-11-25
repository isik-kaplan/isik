"""The scheduled tasks over REST - staff doing to each what its declaration allows, and refused the rest."""

import uuid
from datetime import datetime, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from isik.django.apps.scheduling import state
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.scheduling import Source, StaffMay
from tests.django.apps.scheduling.conftest import CLEANUP, row_of
from tests.testapp.models import EmailUser


pytestmark = pytest.mark.django_db

SWEEP = "isik.features.sweep"
URL = f"/scheduled-tasks/{CLEANUP}/"


class KeyedClient(APIClient):
    """Every write under a key of its own, unless it names one - the API asks for it."""

    def generic(self, method, path, *args, headers=None, **kwargs):
        if method in ("POST", "PATCH"):
            headers = {"Idempotency-Key": str(uuid.uuid4()), **(headers or {})}
        return super().generic(method, path, *args, headers=headers, **kwargs)


@pytest.fixture
def client(staff, rows):
    client = KeyedClient()
    client.force_authenticate(staff)
    return client


@pytest.fixture
def sent(monkeypatch):
    from celery import current_app

    sent = []
    monkeypatch.setattr(current_app, "send_task", lambda name, **kwargs: sent.append(name))
    return sent


def etag(client, url=URL):
    return client.get(url)["ETag"]


class TestWhoMay:
    def test_a_stranger(self, rows):
        assert APIClient().get("/scheduled-tasks/").status_code in (401, 403)

    def test_someone_not_staff(self, rows):
        client = APIClient()
        client.force_authenticate(EmailUser.objects.create(username="bob", email="bob@example.com"))

        assert client.get("/scheduled-tasks/").status_code == 403


class TestReading:
    def test_a_task(self, client):
        state.reschedule(CLEANUP, every=timedelta(hours=1))

        response = client.get(URL)

        assert response.status_code == 200
        assert response.json() == {
            "name": CLEANUP,
            "task": "testapp.cleanup",
            "code": {"every": None, "crontab": "0 3 * * *", "paused": False, "on_restart": "code"},
            "saved": {"every": 3600, "crontab": None, "paused": False, "on_restart": None},
            "effective": {"every": 3600, "crontab": None, "paused": False, "on_restart": "code"},
            "may": ["view", "pause", "run_now", "reschedule", "switch_on_restart"],
            "run_by_hand_at": None,
            "last_run_at": None,
        }

    def test_its_last_run(self, client):
        ran = timezone.now()
        get_task_state_model()._default_manager.filter(name=CLEANUP).update(last_run_at=ran)

        assert datetime.fromisoformat(client.get(URL).json()["last_run_at"]) == ran

    def test_a_run_leaves_its_etag(self, client):
        before = etag(client)
        get_task_state_model()._default_manager.filter(name=CLEANUP).update(last_run_at=timezone.now())

        assert etag(client) == before

    def test_every_one_staff_may_view(self, client, changed):
        changed(SWEEP, staff_may=StaffMay.PAUSE)

        names = [task["name"] for task in client.get("/scheduled-tasks/").json()]

        assert CLEANUP in names
        assert SWEEP not in names
        assert names == sorted(names)

    def test_one_staff_may_not_view(self, client, changed):
        changed(SWEEP, staff_may=StaffMay.PAUSE)

        response = client.get(f"/scheduled-tasks/{SWEEP}/")

        assert response.status_code == 403
        assert response.json()["code"] == "not_allowed_by_declaration"
        assert "doesn't let staff view it - it allows: pause." in response.json()["detail"]

    def test_one_nothing_declares(self, client):
        assert client.get("/scheduled-tasks/nothing/").status_code == 404

    def test_a_row_nothing_declares_anymore(self, client):
        from isik.django.apps.scheduling.swapping import get_task_state_model

        get_task_state_model()._default_manager.create(name="gone")

        assert client.get("/scheduled-tasks/gone/").status_code == 404


class TestActions:
    @pytest.mark.parametrize(("path", "paused"), [("pause", True), ("resume", False)])
    def test_pause_and_resume(self, client, path, paused):
        response = client.post(f"{URL}{path}/")

        assert (response.status_code, response.json()["saved"]["paused"]) == (200, paused)
        assert row_of(CLEANUP).paused is paused

    def test_run(self, client, sent, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(f"{URL}run/")

        assert response.status_code == 200
        assert response.json()["run_by_hand_at"] is not None
        assert sent == ["testapp.cleanup"]

    def test_reset(self, client):
        state.reschedule(CLEANUP, every=timedelta(hours=1))

        response = client.post(f"{URL}reset/")

        assert response.json()["saved"]["every"] is None

    def test_reset_at_restart(self, client):
        state.set_on_restart(CLEANUP, Source.DATABASE)

        response = client.post(f"{URL}reset-at-restart/")

        assert response.json()["saved"]["on_restart"] == "code"

    @pytest.mark.parametrize("path", ["pause", "resume", "run", "reset", "reset-at-restart"])
    def test_refused_what_the_declaration_doesnt_allow(self, client, changed, path):
        changed(CLEANUP, staff_may=StaffMay.VIEW, source_editable=False)

        response = client.post(f"{URL}{path}/")

        assert response.status_code == 403
        assert response.json()["code"] == "not_allowed_by_declaration"
        assert response.json()["detail"].endswith("it allows: view.")


class TestChanging:
    def test_rescheduled_every_so_often(self, client):
        response = client.patch(URL, {"every": 600}, format="json", headers={"If-Match": etag(client)})

        assert response.status_code == 200
        assert response.json()["saved"]["every"] == 600
        assert row_of(CLEANUP).every == timedelta(minutes=10)

    def test_rescheduled_at_a_crontab(self, client):
        response = client.patch(URL, {"crontab": "*/5 * * * *"}, format="json", headers={"If-Match": etag(client)})

        assert response.json()["effective"]["crontab"] == "*/5 * * * *"

    def test_the_restart_mode_switched(self, client):
        response = client.patch(URL, {"on_restart": "database"}, format="json", headers={"If-Match": etag(client)})

        assert response.json()["effective"]["on_restart"] == "database"

    def test_both_at_once(self, client):
        response = client.patch(
            URL, {"every": 60, "on_restart": "database"}, format="json", headers={"If-Match": etag(client)}
        )

        assert response.status_code == 200, response.json()
        assert (response.json()["saved"]["every"], response.json()["saved"]["on_restart"]) == (60, "database")

    def test_nothing_changes_nothing(self, client):
        response = client.patch(URL, {}, format="json", headers={"If-Match": etag(client)})

        assert (response.status_code, response.json()["name"]) == (200, CLEANUP)

    def test_it_asks_if_match(self, client):
        assert client.patch(URL, {"every": 600}, format="json").status_code == 428

    def test_both_schedules(self, client):
        response = client.patch(
            URL, {"every": 60, "crontab": "0 3 * * *"}, format="json", headers={"If-Match": etag(client)}
        )

        assert response.status_code == 400
        assert response.json() == {"non_field_errors": ["Give every or crontab, not both."]}

    def test_a_crontab_that_isnt_one(self, client):
        response = client.patch(URL, {"crontab": "nightly"}, format="json", headers={"If-Match": etag(client)})

        assert response.status_code == 400
        assert "isn't a crontab" in response.json()["crontab"][0]

    @pytest.mark.parametrize("every", [0, -5])
    def test_every_at_least_a_second(self, client, every):
        response = client.patch(URL, {"every": every}, format="json", headers={"If-Match": etag(client)})

        assert response.status_code == 400

    def test_refused_rescheduling(self, client, changed):
        changed(CLEANUP, staff_may=StaffMay.VIEW)

        response = client.patch(URL, {"every": 60}, format="json", headers={"If-Match": etag(client)})

        assert (response.status_code, response.json()["code"]) == (403, "not_allowed_by_declaration")
        assert row_of(CLEANUP).every is None

    def test_refused_switching_the_restart_mode(self, client, changed):
        changed(CLEANUP, source_editable=False)

        response = client.patch(URL, {"on_restart": "database"}, format="json", headers={"If-Match": etag(client)})

        assert response.status_code == 403
        assert row_of(CLEANUP).on_restart is None


def test_a_run_retried_under_its_key_runs_once(client, sent, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        key = str(uuid.uuid4())
        first = client.post(f"{URL}run/", headers={"Idempotency-Key": key})
        again = client.post(f"{URL}run/", headers={"Idempotency-Key": key})

    assert (first.status_code, again.status_code) == (200, 200), (first.json(), again.json())
    assert sent == ["testapp.cleanup"]


def test_a_change_refused_undoes_the_others(client, changed):
    changed(CLEANUP, source_editable=False)

    response = client.patch(
        URL, {"every": 60, "on_restart": "database"}, format="json", headers={"If-Match": etag(client)}
    )

    assert response.status_code == 403
    assert row_of(CLEANUP).every is None
