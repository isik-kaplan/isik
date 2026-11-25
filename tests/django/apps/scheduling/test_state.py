"""A declared task's state - its declaration and its row - reconciled at migrate and at a start, and what staff do."""

import logging
from datetime import timedelta

import pghistory.models
import pytest

from isik.django.apps.scheduling import state
from isik.django.apps.scheduling.state import (
    RECONCILED,
    NotAllowed,
    State,
    UnknownTask,
    declared_state,
    effective_state,
    may,
    on_restart_of,
    reconcile,
)
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.scheduling import ScheduledTask, Source, StaffMay, declared_tasks
from tests.django.apps.scheduling.conftest import CLEANUP, row_of


pytestmark = pytest.mark.django_db

SWEEP = "isik.features.sweep"
ALL = StaffMay.VIEW | StaffMay.PAUSE | StaffMay.RUN_NOW | StaffMay.RESCHEDULE


def task(**options):
    return ScheduledTask("t", "app.t", **{"every": timedelta(minutes=5), **options})


def row(**fields):
    return get_task_state_model()(**{"name": "t", "paused": False, **fields})


class TestAState:
    def test_as_declared(self):
        declared = task(crontab=None, paused=True, on_restart=Source.DATABASE)

        assert declared_state(declared) == State(timedelta(minutes=5), None, True, Source.DATABASE)

    def test_its_schedule(self):
        assert State(None, "0 3 * * *", False, Source.CODE).schedule.hour == {3}
        assert State(timedelta(1), None, False, Source.CODE).schedule == timedelta(1)

    def test_with_no_row_the_declarations(self):
        declared = task(paused=True)

        assert effective_state(declared, None) == declared_state(declared)

    def test_the_rows_schedule_and_pause(self):
        assert effective_state(task(), row(every=timedelta(hours=1), paused=True)) == State(
            timedelta(hours=1), None, True, Source.CODE
        )
        assert effective_state(task(), row(crontab="0 3 * * *")) == State(None, "0 3 * * *", False, Source.CODE)

    def test_no_schedule_given_the_declarations(self):
        assert effective_state(task(), row()) == State(timedelta(minutes=5), None, False, Source.CODE)


class TestTheRestartMode:
    def test_the_rows_where_the_declaration_lets_staff_choose(self):
        assert on_restart_of(task(source_editable=True), row(on_restart="database")) is Source.DATABASE

    @pytest.mark.parametrize(
        ("declared", "saved"),
        [
            (task(source_editable=False), row(on_restart="database")),
            (task(source_editable=True), row(on_restart=None)),
            (task(source_editable=True), None),
        ],
    )
    def test_else_the_declarations(self, declared, saved):
        assert on_restart_of(declared, saved) is Source.CODE


class TestMay:
    def test_what_it_allows(self):
        assert may(task(staff_may=StaffMay.VIEW | StaffMay.RUN_NOW)) == ["view", "run_now"]
        assert may(task(staff_may=ALL, source_editable=True)) == [
            "view",
            "pause",
            "run_now",
            "reschedule",
            "switch_on_restart",
        ]
        assert may(task(staff_may=StaffMay(0))) == []


class TestReconciling:
    def test_every_declared_task_gets_its_row_seeded(self, db, changed):
        get_task_state_model()._default_manager.all().delete()
        changed(SWEEP, paused=True)

        assert reconcile(restart=False) == {}

        saved = {each.name: each for each in get_task_state_model()._default_manager.all()}
        assert set(saved) == set(declared_tasks())
        assert (saved[SWEEP].paused, saved[CLEANUP].paused) == (True, False)
        assert (saved[SWEEP].every, saved[SWEEP].crontab, saved[SWEEP].on_restart) == (None, None, None)

    def test_a_restart_mode_the_code_no_longer_lets_staff_choose(self, rows, changed, caplog):
        state.set_on_restart(CLEANUP, Source.DATABASE)
        changed(CLEANUP, source_editable=False)

        with caplog.at_level(logging.WARNING, logger=state.__name__):
            changes = reconcile(restart=False)

        message = "on_restart database set back to the declaration's code - the code no longer lets staff choose"
        assert changes == {CLEANUP: [message]}
        assert row_of(CLEANUP).on_restart is None
        assert [record.getMessage() for record in caplog.records] == [f"{CLEANUP}: {message}"]

    def test_at_a_start_a_code_task_gets_its_schedule_and_pause_back(self, rows):
        state.reschedule(CLEANUP, every=timedelta(hours=2))
        state.pause(CLEANUP)

        assert reconcile(restart=False) == {}
        assert reconcile(restart=True) == {
            CLEANUP: ["schedule and pause set back to the code's at the scheduler's start"]
        }
        saved = row_of(CLEANUP)
        assert (saved.every, saved.crontab, saved.paused) == (None, None, False)

    @pytest.mark.parametrize(
        "moved", [{"every": timedelta(hours=2)}, {"crontab": "1 * * * *"}, {"paused": True}], ids=str
    )
    def test_any_of_the_three_moved_is_set_back(self, rows, moved):
        get_task_state_model()._default_manager.filter(name=CLEANUP).update(**moved)

        assert list(reconcile(restart=True)) == [CLEANUP]

    def test_a_declaration_starting_paused_stays_paused(self, rows, changed):
        changed(SWEEP, paused=True)
        get_task_state_model()._default_manager.filter(name=SWEEP).update(paused=True)

        assert reconcile(restart=True) == {}

    def test_a_database_task_keeps_what_staff_saved(self, rows):
        state.set_on_restart(CLEANUP, Source.DATABASE)
        state.reschedule(CLEANUP, every=timedelta(hours=2))
        state.pause(CLEANUP)

        assert reconcile(restart=True) == {}
        assert (row_of(CLEANUP).every, row_of(CLEANUP).paused) == (timedelta(hours=2), True)

    def test_both_at_once(self, rows, changed):
        state.set_on_restart(CLEANUP, Source.DATABASE)
        state.pause(CLEANUP)
        changed(CLEANUP, source_editable=False)

        assert len(reconcile(restart=True)[CLEANUP]) == 2
        assert row_of(CLEANUP).paused is False

    def test_in_the_history_as_reconciled(self, rows):
        state.pause(CLEANUP)
        reconcile(restart=True)

        latest = row_of(CLEANUP).events.order_by("-pgh_id").first()
        assert pghistory.models.Context.objects.get(pk=latest.pgh_context_id).metadata["reason"] == RECONCILED

    def test_in_one_transaction(self, rows, monkeypatch):
        state.pause(CLEANUP)
        state.pause(SWEEP)
        model = get_task_state_model()
        real, saves = model.save, []

        def failing_second(self, *args, **kwargs):
            saves.append(self.name)
            if len(saves) == 2:
                raise RuntimeError("down")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(model, "save", failing_second)
        with pytest.raises(RuntimeError):
            reconcile(restart=True)

        assert (row_of(CLEANUP).paused, row_of(SWEEP).paused) == (True, True)


class TestWhatStaffDo:
    def test_an_unknown_task(self, rows):
        with pytest.raises(UnknownTask) as refused:
            state.pause("nothing")

        assert str(refused.value) == "No scheduled task called nothing is declared."

    def test_pause_and_resume(self, rows):
        assert state.pause(CLEANUP).paused is True
        assert row_of(CLEANUP).paused is True
        assert state.resume(CLEANUP).paused is False
        assert row_of(CLEANUP).paused is False

    def test_reschedule(self, rows):
        state.reschedule(CLEANUP, crontab="*/5 * * * *")
        assert (row_of(CLEANUP).every, row_of(CLEANUP).crontab) == (None, "*/5 * * * *")

        state.reschedule(CLEANUP, every=timedelta(hours=2))
        assert (row_of(CLEANUP).every, row_of(CLEANUP).crontab) == (timedelta(hours=2), None)

    @pytest.mark.parametrize("given", [{}, {"every": timedelta(1), "crontab": "0 3 * * *"}])
    def test_reschedule_takes_one_schedule(self, rows, given):
        with pytest.raises(ValueError) as refused:
            state.reschedule(CLEANUP, **given)

        assert str(refused.value) == "Give every= or crontab=, one of them."

    def test_reschedule_to_a_crontab_that_isnt_one(self, rows):
        with pytest.raises(ValueError):
            state.reschedule(CLEANUP, crontab="nightly")

    def test_a_row_made_where_there_is_none(self, db, changed):
        get_task_state_model()._default_manager.all().delete()
        changed(CLEANUP, paused=True)

        state.reschedule(CLEANUP, every=timedelta(hours=2))

        assert (row_of(CLEANUP).paused, row_of(CLEANUP).every) == (True, timedelta(hours=2))

    def test_switching_the_restart_mode(self, rows):
        assert state.set_on_restart(CLEANUP, Source.DATABASE).on_restart == "database"
        assert row_of(CLEANUP).on_restart == "database"
        state.reset_at_restart(CLEANUP)
        assert row_of(CLEANUP).on_restart == "code"

    def test_reset_now(self, rows):
        state.reschedule(CLEANUP, every=timedelta(hours=2))
        state.pause(CLEANUP)
        state.set_on_restart(CLEANUP, Source.DATABASE)

        state.reset(CLEANUP)

        saved = row_of(CLEANUP)
        assert (saved.every, saved.crontab, saved.paused, saved.on_restart) == (None, None, False, "database")

    def test_reset_back_to_a_declared_pause(self, rows, changed):
        changed(CLEANUP, paused=True)
        state.resume(CLEANUP)

        assert state.reset(CLEANUP).paused is True

    @pytest.mark.parametrize(
        ("allowed", "kept"),
        [(StaffMay.VIEW | StaffMay.PAUSE, ("every",)), (StaffMay.VIEW | StaffMay.RESCHEDULE, ("paused",))],
    )
    def test_reset_only_what_staff_may_change(self, rows, changed, allowed, kept):
        get_task_state_model()._default_manager.filter(name=CLEANUP).update(every=timedelta(hours=2), paused=True)
        changed(CLEANUP, staff_may=allowed)

        state.reset(CLEANUP)

        saved = row_of(CLEANUP)
        assert (saved.every == timedelta(hours=2), saved.paused) == ("every" in kept, "paused" in kept)

    def test_run_now_once_committed(self, rows, monkeypatch, django_capture_on_commit_callbacks):
        from celery import current_app

        sent = []
        monkeypatch.setattr(current_app, "send_task", lambda name, **kwargs: sent.append((name, kwargs)))

        with django_capture_on_commit_callbacks(execute=True):
            saved = state.run_now(CLEANUP)
            assert sent == []

        assert sent == [("testapp.cleanup", {"args": [], "kwargs": {"days": 7}})]
        assert saved.run_by_hand_at is not None
        assert row_of(CLEANUP).run_by_hand_at == saved.run_by_hand_at

    def test_run_now_with_its_options(self, rows, changed, monkeypatch, django_capture_on_commit_callbacks):
        from celery import current_app

        sent = []
        monkeypatch.setattr(current_app, "send_task", lambda name, **kwargs: sent.append(kwargs))
        changed(CLEANUP, args=(1, 2), options={"queue": "slow"})

        with django_capture_on_commit_callbacks(execute=True):
            state.run_now(CLEANUP)

        assert sent == [{"args": [1, 2], "kwargs": {"days": 7}, "queue": "slow"}]


class TestRefused:
    @pytest.mark.parametrize(
        ("allowed", "do", "wanted"),
        [
            (StaffMay.VIEW, state.pause, "pause"),
            (StaffMay.VIEW, state.resume, "resume"),
            (StaffMay.VIEW, state.run_now, "run"),
            (StaffMay.VIEW, lambda name: state.reschedule(name, every=timedelta(1)), "reschedule"),
            (StaffMay.VIEW | StaffMay.RUN_NOW, state.reset, "reset"),
        ],
    )
    def test_what_the_declaration_doesnt_allow(self, rows, changed, allowed, do, wanted):
        changed(CLEANUP, staff_may=allowed, source_editable=False)

        with pytest.raises(NotAllowed) as refused:
            do(CLEANUP)

        allows = ", ".join(may(declared_tasks()[CLEANUP]))
        assert str(refused.value) == (f"{CLEANUP}'s declaration doesn't let staff {wanted} it - it allows: {allows}.")
        assert refused.value.code == "not_allowed_by_declaration"

    @pytest.mark.parametrize("do", [lambda name: state.set_on_restart(name, Source.DATABASE), state.reset_at_restart])
    def test_switching_the_restart_mode_where_the_code_decides(self, rows, changed, do):
        changed(CLEANUP, source_editable=False)

        with pytest.raises(NotAllowed) as refused:
            do(CLEANUP)

        assert "doesn't let staff switch the restart mode of it" in str(refused.value)
        assert row_of(CLEANUP).on_restart is None

    def test_allowing_nothing(self, rows, changed):
        changed(CLEANUP, staff_may=StaffMay(0), source_editable=False)

        with pytest.raises(NotAllowed) as refused:
            state.pause(CLEANUP)

        assert str(refused.value).endswith("it allows: nothing.")
