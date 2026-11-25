"""Scheduled tasks declared in code - isik's own and a project's - and the beat schedule built from them."""

import gc
import os
import subprocess
import sys
import types
from datetime import timedelta

import pytest
from celery import Celery
from celery.schedules import crontab

from isik.django import scheduling
from isik.django.conf import isik_areas
from isik.django.scheduling import (
    ISIK_TASKS,
    SCHEDULER,
    ScheduledTask,
    Source,
    StaffMay,
    beat_runs,
    beat_schedule,
    celery_schedule,
    crontab_fields,
    declared_tasks,
    merge_beat_schedule,
    override_scheduled_task,
    scheduled_task,
    uses_isiks_scheduler,
)


NOTIFICATIONS = "isik.django.apps.notifications"
FEATURES = "isik.django.apps.features"
ACCOUNTS = "isik.django.apps.accounts"


@pytest.fixture
def installed(monkeypatch):
    """Sets INSTALLED_APPS as declared_tasks() reads it - without reloading the app registry, which these apps
    aren't."""
    from django.conf import settings

    return lambda *apps: monkeypatch.setattr(settings, "INSTALLED_APPS", list(apps))


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(scheduling, "declared", {})
    monkeypatch.setattr(scheduling, "overrides", {})


class TestASchedule:
    def test_a_crontabs_fields(self):
        assert crontab_fields("0 3 * * 1-5") == {
            "minute": "0",
            "hour": "3",
            "day_of_month": "*",
            "month_of_year": "*",
            "day_of_week": "1-5",
        }

    @pytest.mark.parametrize("expression", ["0 3 * *", "0 3 * * * *", ""])
    def test_a_crontab_without_five_fields(self, expression):
        with pytest.raises(ValueError) as refused:
            crontab_fields(expression)

        assert str(refused.value) == (
            f"{expression!r} isn't a crontab - it takes five fields: minute hour day-of-month month day-of-week."
        )

    def test_every_so_often(self):
        assert celery_schedule(timedelta(minutes=5), None) == timedelta(minutes=5)

    def test_at_a_crontab(self):
        assert celery_schedule(None, "0 3 * * 1") == crontab(minute="0", hour="3", day_of_week="1")

    def test_none(self):
        assert celery_schedule(None, None) is None


class TestATask:
    def test_its_defaults(self):
        task = ScheduledTask("renew", "billing.renew")

        assert (task.every, task.crontab, task.args, task.kwargs, task.options) == (None, None, (), {}, {})
        assert task.staff_may == StaffMay.VIEW | StaffMay.PAUSE
        assert (task.on_restart, task.source_editable, task.paused) == (Source.CODE, False, False)
        assert task.schedule is None

    def test_its_beat_entry(self):
        task = ScheduledTask(
            "renew", "billing.renew", every=timedelta(hours=1), args=(1,), kwargs={"a": 2}, options={"queue": "q"}
        )

        assert task.beat_entry() == {
            "task": "billing.renew",
            "schedule": timedelta(hours=1),
            "args": (1,),
            "kwargs": {"a": 2},
            "options": {"queue": "q"},
        }

    def test_the_values(self):
        assert [source.value for source in Source] == ["code", "database"]
        assert [member.name for member in StaffMay] == ["VIEW", "PAUSE", "RUN_NOW", "RESCHEDULE"]


class TestDeclaring:
    def test_a_task(self):
        task = scheduled_task("renew", task="billing.renew", crontab="0 3 * * *", staff_may=StaffMay.VIEW)

        assert scheduling.declared == {"renew": task}
        assert (task.name, task.task, task.crontab, task.staff_may) == (
            "renew",
            "billing.renew",
            "0 3 * * *",
            StaffMay.VIEW,
        )

    def test_one_name_once(self):
        scheduled_task("renew", task="billing.renew", every=timedelta(hours=1))

        with pytest.raises(ValueError) as refused:
            scheduled_task("renew", task="billing.other", every=timedelta(hours=1))

        assert str(refused.value) == "A scheduled task called renew is declared already."

    def test_both_kinds_of_schedule(self):
        with pytest.raises(TypeError) as refused:
            scheduled_task("renew", task="billing.renew", every=timedelta(hours=1), crontab="0 3 * * *")

        assert str(refused.value) == (
            "A scheduled task runs every so often or at a crontab - give every= or crontab=, not both."
        )

    def test_no_schedule(self):
        with pytest.raises(TypeError) as refused:
            scheduled_task("renew", task="billing.renew")

        assert str(refused.value) == "renew needs a schedule - every= or crontab=."

    def test_a_crontab_that_isnt_one(self):
        with pytest.raises(ValueError):
            scheduled_task("renew", task="billing.renew", crontab="every night")


class TestOverriding:
    def test_one_kind_of_schedule_replaces_the_other(self):
        override_scheduled_task("isik.features.sweep", crontab="*/5 * * * *")

        assert scheduling.overrides == {"isik.features.sweep": {"crontab": "*/5 * * * *", "every": None}}

        override_scheduled_task("isik.features.sweep", every=timedelta(hours=1))

        assert scheduling.overrides == {"isik.features.sweep": {"every": timedelta(hours=1), "crontab": None}}

    def test_changes_add_up(self):
        override_scheduled_task("isik.features.sweep", staff_may=StaffMay.VIEW)
        override_scheduled_task("isik.features.sweep", paused=True)

        assert scheduling.overrides == {"isik.features.sweep": {"staff_may": StaffMay.VIEW, "paused": True}}

    def test_both_kinds_of_schedule(self):
        with pytest.raises(TypeError):
            override_scheduled_task("isik.features.sweep", every=timedelta(hours=1), crontab="0 3 * * *")

    def test_a_crontab_that_isnt_one(self):
        with pytest.raises(ValueError):
            override_scheduled_task("isik.features.sweep", crontab="nightly")


class TestTheDeclaredTasks:
    def test_isiks_own_for_each_app_installed(self):
        tasks = declared_tasks([FEATURES, ACCOUNTS])

        assert list(tasks) == ["isik.features.sweep", "isik.accounts.sweep_deletions"]
        assert tasks["isik.features.sweep"].every == timedelta(minutes=1)
        assert tasks["isik.accounts.sweep_deletions"].schedule is None

    def test_isiks_own(self):
        assert {app: [(task.name, task.every) for task in own] for app, own in ISIK_TASKS.items()} == {
            NOTIFICATIONS: [
                ("isik.notifications.fan_out", timedelta(seconds=10)),
                ("isik.notifications.reap_stranded", timedelta(minutes=1)),
                ("isik.notifications.prune_attempts", timedelta(hours=1)),
                ("isik.notifications.prune_notices", timedelta(hours=1)),
            ],
            FEATURES: [("isik.features.sweep", timedelta(minutes=1))],
            ACCOUNTS: [("isik.accounts.sweep_deletions", None)],
            "isik.django.apps.inbound": [("isik.inbound.work", None)],
        }
        assert all(task.task == task.name for own in ISIK_TASKS.values() for task in own)

    def test_installed_apps_unless_given(self, installed):
        installed(FEATURES)

        assert list(declared_tasks()) == ["isik.features.sweep"]

    def test_the_projects_after_isiks_changed_as_said(self):
        renew = scheduled_task("renew", task="billing.renew", every=timedelta(hours=1))
        override_scheduled_task("isik.accounts.sweep_deletions", every=timedelta(hours=6))
        override_scheduled_task("renew", paused=True)

        tasks = declared_tasks([ACCOUNTS])

        assert list(tasks) == ["isik.accounts.sweep_deletions", "renew"]
        assert tasks["isik.accounts.sweep_deletions"].every == timedelta(hours=6)
        assert tasks["renew"].paused is True
        assert renew.paused is False

    def test_a_project_declaring_isiks_name(self):
        scheduled_task("isik.features.sweep", task="mine", every=timedelta(hours=1))

        with pytest.raises(ValueError) as refused:
            declared_tasks([FEATURES])

        assert str(refused.value) == (
            "isik.features.sweep is isik's own scheduled task - change it with override_scheduled_task()."
        )

    def test_changing_one_nothing_declares(self):
        override_scheduled_task("isik.features.sweep", paused=True)

        with pytest.raises(ValueError) as refused:
            declared_tasks([ACCOUNTS])

        assert str(refused.value) == "No scheduled task called isik.features.sweep is declared to change."

    def test_the_projects_module_is_imported(self, settings, monkeypatch):
        imported = []
        monkeypatch.setattr(scheduling, "import_module", imported.append)
        isik_areas(settings).SCHEDULING = {"TASKS": "billing.scheduled"}

        declared_tasks([])

        assert imported == ["billing.scheduled"]

    def test_no_module_none_imported(self, monkeypatch, settings):
        isik_areas(settings).SCHEDULING = {}
        monkeypatch.setattr(scheduling, "import_module", lambda name: pytest.fail("imported"))

        declared_tasks([])


class TestTheBeatSchedule:
    def test_every_task_with_a_schedule(self, installed):
        installed(FEATURES, ACCOUNTS)

        assert beat_schedule() == {"isik.features.sweep": declared_tasks()["isik.features.sweep"].beat_entry()}

    def test_isiks_scheduler(self):
        assert SCHEDULER == "isik.django.apps.scheduling.scheduler:Scheduler"
        assert uses_isiks_scheduler(types.SimpleNamespace(beat_scheduler=SCHEDULER)) is True
        assert uses_isiks_scheduler(types.SimpleNamespace(beat_scheduler="celery.beat:PersistentScheduler")) is False

    def test_merged_once_the_app_is_configured(self, installed):
        installed(FEATURES)
        app = Celery("merging", set_as_current=False)
        app.conf.beat_schedule = {"renew": {"task": "app.renew", "schedule": 60}}

        merge_beat_schedule(app)
        assert app.configured is False
        gc.collect()

        assert app.conf.beat_schedule == {"renew": {"task": "app.renew", "schedule": 60}, **beat_schedule()}

    def test_merged_into_an_app_with_no_schedule_of_its_own(self, installed):
        installed(FEATURES)
        app = Celery("merging", set_as_current=False)

        merge_beat_schedule(app)

        assert app.conf.beat_schedule == beat_schedule()

    def test_not_merged_for_isiks_scheduler(self, installed):
        installed(FEATURES)
        app = Celery("merging", set_as_current=False)
        app.conf.beat_scheduler = SCHEDULER

        merge_beat_schedule(app)

        assert app.conf.beat_schedule == {}

    def test_imported_before_django_is_set_up(self):
        # What a project's celery.py does - before django.setup(), with no settings at all.
        run = subprocess.run(
            [sys.executable, "-I", "-c", "import isik.django.scheduling"],
            capture_output=True,
            text=True,
            env={key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"},
        )

        assert run.returncode == 0, run.stderr


class TestWhatBeatRuns:
    @staticmethod
    def conf(schedule=None, scheduler="celery.beat:PersistentScheduler"):
        return types.SimpleNamespace(beat_schedule=schedule, beat_scheduler=scheduler)

    def test_in_its_beat_schedule(self):
        assert beat_runs("app.work", self.conf({"x": {"task": "app.work"}})) is True
        assert beat_runs("app.work", self.conf({"x": {"task": "app.other"}})) is False
        assert beat_runs("app.work", self.conf()) is False

    def test_declared_for_isiks_scheduler(self, installed):
        installed(FEATURES, ACCOUNTS)

        assert beat_runs("isik.features.sweep", self.conf(scheduler=SCHEDULER)) is True
        assert beat_runs("isik.features.sweep", self.conf()) is False

    def test_declared_with_no_schedule_doesnt_run(self, installed):
        installed(ACCOUNTS)

        assert beat_runs("isik.accounts.sweep_deletions", self.conf(scheduler=SCHEDULER)) is False


def test_isiks_own_task_built():
    assert scheduling._isik_task("isik.x", timedelta(seconds=3)) == ScheduledTask(
        "isik.x", "isik.x", every=timedelta(seconds=3)
    )
    assert scheduling._isik_task("isik.y") == ScheduledTask("isik.y", "isik.y")


def test_a_schedule_given_drops_the_other_kind(fresh):
    override_scheduled_task("isik.features.sweep", paused=True)
    override_scheduled_task("isik.features.sweep", crontab="0 3 * * *")

    assert scheduling.overrides == {"isik.features.sweep": {"paused": True, "every": None, "crontab": "0 3 * * *"}}
