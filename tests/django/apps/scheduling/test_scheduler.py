"""isik's beat scheduler: the declared tasks as their rows say, read again every beat_max_loop_interval."""

from datetime import timedelta

import pytest
from celery import current_app
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

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


class TestItsLastRuns:
    """Each task's last run, kept on its row - so a restart keeps its clock."""

    def test_each_run_written_to_its_row(self, started):
        entry = started.schedule[SWEEP]

        new = started.reserve(entry)

        assert new.last_run_at is not None
        assert row_of(SWEEP).last_run_at == new.last_run_at
        assert started.schedule[SWEEP] is new
        assert row_of(CLEANUP).last_run_at is None

    def test_a_write_moves_neither_its_etag_nor_its_history(self, started):
        before = row_of(SWEEP)
        events = get_task_state_model().pgh_event_model.objects.count()

        started.reserve(started.schedule[SWEEP])

        assert row_of(SWEEP).updated_at == before.updated_at
        assert get_task_state_model().pgh_event_model.objects.count() == events

    def test_a_start_reads_them_back(self, rows, clock, monkeypatch):
        ran = timezone.now() - timedelta(hours=5)
        get_task_state_model()._default_manager.filter(name=SWEEP).update(last_run_at=ran)
        monkeypatch.setattr(current_app.conf, "beat_schedule", {})

        assert Scheduler(app=current_app, max_interval=60).schedule[SWEEP].last_run_at == ran

    def test_one_never_run_starts_now(self, started):
        assert started.schedule[CLEANUP].last_run_at is not None

    def test_a_resumed_one_reads_its_row(self, started, clock):
        ran = timezone.now() - timedelta(days=2)
        state.pause(CLEANUP)
        clock["at"] += 60
        assert CLEANUP not in started.schedule
        get_task_state_model()._default_manager.filter(name=CLEANUP).update(last_run_at=ran)
        state.resume(CLEANUP)
        clock["at"] += 60

        assert started.schedule[CLEANUP].last_run_at == ran

    def test_old_connections_are_closed_before_writing(self, started, monkeypatch):
        closed = []
        monkeypatch.setattr(module, "close_old_connections", lambda: closed.append(True))

        started.reserve(started.data[SWEEP])

        assert closed == [True]


class TestEachEntry:
    def test_its_name_args_and_options(self, started, changed):
        changed(CLEANUP, args=(1,), options={"queue": "slow"})
        started._read()

        entry = started.schedule[CLEANUP]
        assert (entry.name, entry.args, entry.options) == (CLEANUP, (1,), {"queue": "slow"})

    def test_a_paused_one_skips_only_itself(self, started):
        first = next(iter(declared_tasks()))
        state.set_on_restart(CLEANUP, state.Source.DATABASE)
        get_task_state_model()._default_manager.filter(name=first).update(paused=True)
        started._read()

        assert first not in started.schedule
        assert set(started.schedule) == set(declared_tasks()) - {first}
