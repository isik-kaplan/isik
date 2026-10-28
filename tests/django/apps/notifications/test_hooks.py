"""Starting a fan-out without a beat schedule: after a writing request, after a task, and from a cron."""

from datetime import timedelta
from io import StringIO

import pytest
from celery.signals import task_postrun
from django.apps import apps as django_apps
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory

from isik.django.apps.notifications import checks, exemptions, hooks, pipeline, schedule
from isik.django.apps.notifications.audiences import Subscribers
from isik.django.apps.notifications.hooks import FanOutAfterWritesMiddleware, fan_out_if_waiting
from isik.django.apps.notifications.kinds import Kind
from isik.django.apps.notifications.management.commands.notices import Command as NoticesCommand
from isik.django.apps.notifications.models import Notice, Policy, Source
from tests.testapp.models import Parcel


pytestmark = pytest.mark.django_db


@pytest.fixture
def waiting(parcel):
    """A notice waiting for a fan-out."""
    Parcel.objects.filter(pk=parcel.pk).update(status="shipped")
    return Notice.objects.get(fanned_out_at__isnull=True)


def test_nothing_waiting_asks_for_nothing(dispatched):
    assert fan_out_if_waiting() is False
    assert dispatched == []


def test_a_waiting_notice_asks_for_a_fan_out(waiting, dispatched):
    assert fan_out_if_waiting() is True
    assert dispatched == [("fan_out", (), {})]


@pytest.mark.parametrize(("method", "asked"), [("post", 1), ("put", 1), ("patch", 1), ("delete", 1), ("get", 0)])
def test_the_middleware_asks_after_a_writing_request(waiting, dispatched, method, asked):
    response, seen = HttpResponse(), []
    middleware = FanOutAfterWritesMiddleware(lambda request: seen.append(request) or response)
    request = getattr(RequestFactory(), method)("/")

    assert middleware(request) is response
    assert seen == [request]
    assert len(dispatched) == asked


class Task:
    def __init__(self, name):
        self.name = name


@pytest.mark.parametrize(
    ("on", "name", "asked"), [(True, "app.work", 1), (True, "isik.notifications.send", 0), (False, "app.work", 0)]
)
def test_a_task_asks_while_the_setting_is_on(waiting, dispatched, settings, on, name, asked):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "FAN_OUT_AFTER_TASKS": on}

    task_postrun.send(sender=Task(name))

    assert len(dispatched) == asked


def test_the_command_fans_out_everything_waiting(waiting, dispatched, settings):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "BATCH_SIZE": 1}
    Notice.objects.create(kind_id="parcel_reminder", variant="daily", snapshot={})
    out = StringIO()

    call_command("notices", "--fan-out", stdout=out)

    assert out.getvalue() == "Fanned out 2 notices.\n"
    assert not Notice.objects.filter(fanned_out_at__isnull=True).exists()


def test_the_work_command_runs_what_a_beat_would(waiting, dispatched, settings):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_RETENTION": timedelta(0)}
    out = StringIO()

    call_command("notices", "--work", stdout=out)

    assert out.getvalue() == (
        "Fanned out 1 notices.\nAsked again for 0 deliveries and 0 shards.\nPruned 0 attempts and 0 notices.\n"
    )
    assert not Notice.objects.filter(fanned_out_at__isnull=True).exists()


def test_the_work_command_prunes_until_nothing_is_left(dispatched, settings, monkeypatch):
    left = {"attempts": [2, 1, 0], "notices": [3, 0]}
    monkeypatch.setattr(pipeline, "prune_attempts", lambda: left["attempts"].pop(0))
    monkeypatch.setattr(pipeline, "prune_notices", lambda: left["notices"].pop(0))
    out = StringIO()

    call_command("notices", "--work", stdout=out)

    assert out.getvalue().endswith("Pruned 3 attempts and 3 notices.\n")


class Beat:
    def __init__(self, schedule=None, eager=False):
        self.beat_schedule, self.task_always_eager = schedule, eager


@pytest.fixture
def celery_conf(monkeypatch):
    def configure(**conf):
        monkeypatch.setattr(hooks, "_celery_conf", lambda: Beat(**conf))

    return configure


@pytest.fixture
def unacknowledged(settings):
    settings.ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Testapp"}
    return settings


class TestNothingStartsAFanOut:
    def test_it_warns(self, unacknowledged, celery_conf):
        celery_conf()

        warning = (
            "Nothing starts a fan-out, so no notice is ever sent: merge beat_schedule() into the Celery app's "
            "beat, add FanOutAfterWritesMiddleware, set FAN_OUT_AFTER_TASKS, or - when a cron or "
            "django-celery-beat runs it - say so with FAN_OUT_BY=ScheduledElsewhere(reason=...)."
        )
        assert [
            (message.id, message.msg, message.hint) for message in checks.notifications_can_work(app_configs=None)
        ] == [("isik.W006", warning, "See isik.django.apps.notifications.")]

    def test_a_merged_beat_schedule_starts_it(self, unacknowledged, celery_conf):
        celery_conf(schedule={"other": {"task": "app.work"}, **schedule.beat_schedule()})

        assert checks.fan_out_warnings() == []

    def test_a_beat_without_it_doesnt(self, unacknowledged, celery_conf):
        celery_conf(schedule={"other": {"task": "app.work"}})

        assert len(checks.fan_out_warnings()) == 1

    def test_the_middleware_starts_it(self, unacknowledged, celery_conf):
        celery_conf()
        unacknowledged.MIDDLEWARE = [*unacknowledged.MIDDLEWARE, hooks.MIDDLEWARE]

        assert checks.fan_out_warnings() == []

    def test_fanning_out_after_tasks_starts_it(self, unacknowledged, celery_conf):
        celery_conf()
        unacknowledged.ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Testapp", "FAN_OUT_AFTER_TASKS": True}

        assert checks.fan_out_warnings() == []

    def test_a_reason_acknowledges_it(self, unacknowledged, celery_conf):
        celery_conf()
        unacknowledged.ISIK_NOTIFICATIONS = {
            "PROJECT_NAME": "Testapp",
            "FAN_OUT_BY": exemptions.ScheduledElsewhere(reason="a cron runs manage.py notices --work every minute"),
        }

        assert checks.fan_out_warnings() == []
        assert checks.setting_problems() == []

    def test_the_real_celery_app_is_read(self):
        from celery import current_app

        assert hooks.fan_out_scheduled() is bool(
            [entry for entry in (current_app.conf.beat_schedule or {}).values() if entry["task"] == hooks.FAN_OUT_TASK]
        )
        assert hooks.runs_inline() is bool(current_app.conf.task_always_eager)


@pytest.mark.parametrize("name", ["FAN_OUT_BY", "SENDS_INLINE"])
@pytest.mark.parametrize("value", ["a cron runs manage.py notices --work every minute", True])
def test_an_acknowledgement_is_a_typed_reason(unacknowledged, name, value):
    unacknowledged.ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Testapp", name: value}

    assert checks.setting_problems() == [
        f"ISIK_NOTIFICATIONS.{name} takes a "
        f"{'ScheduledElsewhere' if name == 'FAN_OUT_BY' else 'SendsInline'}(reason=...), "
        f"{'not a bare string' if value is not True else 'not True'}."
    ]


class TestSendingInline:
    @pytest.fixture
    def inline(self, unacknowledged, celery_conf):
        celery_conf(eager=True)
        unacknowledged.ISIK_NOTIFICATIONS = {"PROJECT_NAME": "Testapp", "FAN_OUT_AFTER_TASKS": True}
        return unacknowledged

    def test_it_warns(self, inline):
        warning = (
            "Celery runs tasks inline (task_always_eager) and a fan-out follows each write, so a webhook is "
            "sent inside the request or task that wrote its notice - an endpoint can hold it for "
            "ENDPOINT_TIMEOUT. Run a worker, or say why that's fine with SENDS_INLINE=SendsInline(reason=...)."
        )
        assert [
            (message.id, message.msg, message.hint) for message in checks.notifications_can_work(app_configs=None)
        ] == [("isik.W007", warning, "See isik.django.apps.notifications.")]

    def test_not_with_a_worker(self, inline, celery_conf):
        celery_conf(eager=False)

        assert checks.inline_send_warnings() == []

    def test_not_without_a_fan_out_after_writes(self, inline, celery_conf):
        inline.ISIK_NOTIFICATIONS = {
            "PROJECT_NAME": "Testapp",
            "FAN_OUT_BY": exemptions.ScheduledElsewhere(reason="x" * 40),
        }

        assert checks.inline_send_warnings() == []

    def test_not_without_a_webhook(self, inline, declaring):
        declaring(
            Kind(
                name="app",
                audience=Subscribers(),
                policy=Policy.MANDATORY,
                source=Source.APPLICATION,
                channels=("inbox",),
            )
        )

        assert checks.inline_send_warnings() == []

    def test_a_reason_acknowledges_it(self, inline):
        inline.ISIK_NOTIFICATIONS = {
            **inline.ISIK_NOTIFICATIONS,
            "SENDS_INLINE": exemptions.SendsInline(reason="webhooks go to our own receiver, on the same host"),
        }

        assert checks.inline_send_warnings() == []


def test_the_app_connects_the_task_hook(waiting, dispatched, settings):
    task_postrun.disconnect(hooks.fan_out_after_task)
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "FAN_OUT_AFTER_TASKS": True}

    django_apps.get_app_config("isik_notifications").ready()
    task_postrun.send(sender=Task("app.work"))

    assert dispatched == [("fan_out", (), {})]


def test_the_options_say_what_they_do():
    parser = NoticesCommand().create_parser("manage.py", "notices")

    assert {action.dest: action.help for action in parser._actions if action.dest in ("fan_out", "work")} == {
        "fan_out": "Fan out every waiting notice now.",
        "work": (
            "What a beat would run, once: fan out every waiting notice, take back what's stranded or overdue, "
            "and prune what's past its retention - for a cron, in a project with no beat."
        ),
    }
