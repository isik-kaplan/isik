"""isik.E031 - scheduled tasks that can't work."""

import pytest
from django.core.management import call_command

from isik.django.apps.scheduling import checks
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.conf import isik_areas
from isik.django.scheduling import StaffMay
from tests.django.apps.scheduling.conftest import CLEANUP


class TestTheSetting:
    def test_keys_it_doesnt_know(self, settings):
        isik_areas(settings).SCHEDULING = {"TASKS": "x", "TASK_MODEL": "y", "B": 1, "A": 2}

        assert checks.setting_problems() == ["ISIK['DJANGO']['SCHEDULING'] has keys it doesn't know: A, B."]

    def test_its_own(self, settings):
        isik_areas(settings).SCHEDULING = {"TASKS": "x", "TASK_MODEL": "y"}

        assert checks.setting_problems() == []


class TestTheDeclarations:
    def test_the_test_projects_can_work(self):
        assert checks.declaration_problems() == []

    def test_one_with_no_schedule(self, changed):
        changed("isik.inbound.work", every=None)

        assert checks.declaration_problems() == [
            "isik.inbound.work has no schedule - isik has none for it. Give it one: "
            "override_scheduled_task('isik.inbound.work', every=...) or crontab=."
        ]

    @pytest.mark.parametrize("allowed", [StaffMay.VIEW, StaffMay.VIEW | StaffMay.RUN_NOW])
    def test_a_restart_mode_with_nothing_to_keep(self, changed, allowed):
        changed(CLEANUP, staff_may=allowed)

        assert checks.declaration_problems() == [
            f"{CLEANUP} lets staff switch its restart mode, and neither pause nor reschedule it - a mode keeping "
            "what staff saved would keep nothing."
        ]

    @pytest.mark.parametrize("allowed", [StaffMay.PAUSE, StaffMay.RESCHEDULE])
    def test_either_is_something_to_keep(self, changed, allowed):
        changed(CLEANUP, staff_may=allowed)

        assert checks.declaration_problems() == []

    def test_a_declaration_refused(self, changed):
        changed("nothing.declared", paused=True)

        assert checks.declaration_problems() == ["No scheduled task called nothing.declared is declared to change."]


class TestTheHistory:
    def test_isiks_model_keeps_one(self):
        assert checks.history_problems() == []

    def test_a_model_without_one(self, monkeypatch):
        class Untracked:
            class _meta:
                label = "ops.ScheduledTaskState"

        monkeypatch.setattr(checks, "get_task_state_model", lambda: Untracked)

        assert checks.history_problems() == [
            "ops.ScheduledTaskState has no history - it says who paused, rescheduled or ran a task. Track it with "
            "@track_events()."
        ]


def test_the_check_reports_each(monkeypatch):
    monkeypatch.setattr(checks, "setting_problems", lambda: ["a"])
    monkeypatch.setattr(checks, "declaration_problems", lambda: ["b"])
    monkeypatch.setattr(checks, "history_problems", lambda: ["c"])

    errors = checks.scheduled_tasks_can_work(app_configs=None)

    assert [(error.id, error.msg, error.hint) for error in errors] == [
        ("isik.E031", "a", "See isik.django.apps.scheduling."),
        ("isik.E031", "b", "See isik.django.apps.scheduling."),
        ("isik.E031", "c", "See isik.django.apps.scheduling."),
    ]


@pytest.mark.django_db
def test_migrate_reconciles(monkeypatch):
    from isik.django.apps.scheduling import apps as module

    asked = []
    monkeypatch.setattr("isik.django.apps.scheduling.state.reconcile", lambda **options: asked.append(options))

    module.reconcile_after_migrating(sender=None)

    assert asked == [{"restart": False}]


@pytest.mark.django_db
def test_migrate_runs_it(monkeypatch):
    get_task_state_model()._default_manager.all().delete()

    call_command("migrate", "isik_scheduling", verbosity=0)

    assert get_task_state_model()._default_manager.filter(name=CLEANUP).exists()


@pytest.mark.django_db
class TestTheCommand:
    def run(self, *args):
        from io import StringIO

        out, err = StringIO(), StringIO()
        call_command("scheduled_tasks", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_each_task_as_the_scheduler_runs_it(self, rows):
        from isik.django.apps.scheduling import state

        state.pause(CLEANUP)
        out, err = self.run()

        lines = {line.split()[0]: line for line in out.splitlines()}
        assert lines[CLEANUP].split(None, 1)[1] == (
            "0 3 * * *, paused  view, pause, run_now, reschedule, switch_on_restart"
        )
        assert lines["isik.features.sweep"].split(None, 1)[1].startswith("0:01:00")
        assert err == ""

    def test_rows_no_longer_declared(self, rows):
        get_task_state_model()._default_manager.create(name="gone")

        _out, err = self.run()

        assert err == "gone has a row and no declaration - --prune deletes it.\n"
        assert get_task_state_model()._default_manager.filter(name="gone").exists()

    def test_pruned(self, rows):
        get_task_state_model()._default_manager.create(name="gone")

        out, _err = self.run("--prune")

        assert out.endswith("Deleted the row of gone, which nothing declares.\n")
        assert not get_task_state_model()._default_manager.filter(name="gone").exists()
        assert get_task_state_model()._default_manager.filter(name=CLEANUP).exists()

    def test_nothing_declared(self, rows, monkeypatch):
        from isik.django.apps.scheduling.management.commands import scheduled_tasks

        monkeypatch.setattr(scheduled_tasks, "declared_tasks", dict)
        out, _err = self.run()

        assert out == ""
