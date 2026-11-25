"""isik's beat scheduler: the declared tasks as their rows say, read again every beat_max_loop_interval."""

from datetime import timedelta

import pytest
from celery import current_app
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

from isik.django.apps.scheduling import scheduler as module
from isik.django.apps.scheduling import state
from isik.django.apps.scheduling.scheduler import Scheduler
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.scheduling import declared_tasks
from tests.django.apps.scheduling.conftest import CLEANUP, row_of


pytestmark = pytest.mark.django_db

SWEEP = "isik.features.sweep"


@pytest.fixture(autouse=True)
def isiks_tasks():
    """isik's apps' task modules - a project's Celery app finds them with autodiscover_tasks()."""
    from isik.django.apps.accounts import tasks as accounts  # noqa: F401
    from isik.django.apps.features import tasks as features  # noqa: F401
    from isik.django.apps.inbound import tasks as inbound  # noqa: F401
    from isik.django.apps.notifications import tasks as notifications  # noqa: F401


@pytest.fixture(autouse=True)
def connections_kept(monkeypatch):
    """A test's connection is inside its transaction, which closing "old" connections would close."""
    monkeypatch.setattr(module, "close_old_connections", lambda: None)


@pytest.fixture
def clock(monkeypatch):
    now = {"at": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: now["at"])
    return now


@pytest.fixture
def started(rows, clock, monkeypatch):
    monkeypatch.setattr(current_app.conf, "beat_schedule", {})
    return Scheduler(app=current_app, max_interval=60)


class TestItsSchedule:
    def test_every_declared_task_with_a_schedule(self, started):
        assert set(started.schedule) == set(declared_tasks())
        cleanup = started.schedule[CLEANUP]
        assert (cleanup.task, cleanup.schedule, cleanup.args, cleanup.kwargs, cleanup.options) == (
            "testapp.cleanup",
            crontab(minute="0", hour="3"),
            (),
            {"days": 7},
            {},
        )
        assert started.schedule[SWEEP].schedule.run_every == timedelta(minutes=1)
        assert started.schedule[SWEEP].app is current_app

    def test_a_paused_one_left_out(self, rows, clock):
        state.set_on_restart(CLEANUP, state.Source.DATABASE)
        state.pause(CLEANUP)

        assert CLEANUP not in Scheduler(app=current_app, max_interval=60).schedule

    def test_as_its_row_says(self, rows, clock):
        state.set_on_restart(CLEANUP, state.Source.DATABASE)
        state.reschedule(CLEANUP, every=timedelta(hours=2))

        assert Scheduler(app=current_app, max_interval=60).schedule[CLEANUP].schedule.run_every == timedelta(hours=2)

    def test_its_start_sets_a_code_task_back(self, rows, clock):
        state.pause(CLEANUP)

        assert CLEANUP in Scheduler(app=current_app, max_interval=60).schedule
        assert row_of(CLEANUP).paused is False

    def test_a_task_with_no_schedule_left_out(self, started, changed):
        changed(SWEEP, every=None)
        started._read()

        assert SWEEP not in started.schedule


class TestReadingItAgain:
    def test_within_the_interval_it_isnt(self, started, clock):
        state.pause(CLEANUP)
        clock["at"] += 59

        assert CLEANUP in started.schedule

    def test_after_it_it_is(self, started, clock):
        state.pause(CLEANUP)
        clock["at"] += 60

        assert CLEANUP not in started.schedule

    def test_an_entrys_last_run_is_kept(self, started, clock):
        ran = started.schedule[SWEEP].last_run_at - timedelta(minutes=5)
        started.data[SWEEP].last_run_at = ran
        clock["at"] += 60

        assert started.schedule[SWEEP].last_run_at == ran

    def test_a_new_entry_starts_now(self, started, clock, changed):
        state.pause(CLEANUP)
        clock["at"] += 60
        assert CLEANUP not in started.schedule
        state.resume(CLEANUP)
        clock["at"] += 60

        assert started.schedule[CLEANUP].last_run_at is not None

    def test_old_connections_are_closed_first(self, started, monkeypatch):
        closed = []
        monkeypatch.setattr(module, "close_old_connections", lambda: closed.append(True))

        started._read()

        assert closed == [True]


class TestRefused:
    def test_a_beat_schedule_of_the_apps_own(self, rows, clock, monkeypatch):
        monkeypatch.setattr(current_app.conf, "beat_schedule", {"b": {}, "a": {}})

        with pytest.raises(ImproperlyConfigured) as refused:
            Scheduler(app=current_app)

        assert str(refused.value) == (
            "The Celery app has a beat schedule of its own beside isik's scheduler: a, b - declare each with "
            "scheduled_task()."
        )

    def test_a_task_celery_doesnt_have(self, rows, clock, changed, monkeypatch):
        monkeypatch.setattr(current_app.conf, "beat_schedule", {})
        changed(CLEANUP, task="testapp.nothing")
        changed(SWEEP, task="testapp.also_nothing")

        with pytest.raises(ImproperlyConfigured) as refused:
            Scheduler(app=current_app)

        assert str(refused.value) == (
            "Scheduled tasks name Celery tasks that don't exist: testapp.also_nothing, testapp.nothing."
        )

    def test_the_apps_task_modules_are_imported_first(self, rows, clock, monkeypatch):
        monkeypatch.setattr(current_app.conf, "beat_schedule", {})
        imported = []
        monkeypatch.setattr(current_app.loader, "import_default_modules", lambda: imported.append(True))

        Scheduler(app=current_app)

        assert imported == [True]

    def test_nothing_reconciled_when_refused(self, rows, clock, monkeypatch):
        monkeypatch.setattr(current_app.conf, "beat_schedule", {"a": {}})
        state.pause(CLEANUP)

        with pytest.raises(ImproperlyConfigured):
            Scheduler(app=current_app)

        assert get_task_state_model()._default_manager.get(name=CLEANUP).paused is True
