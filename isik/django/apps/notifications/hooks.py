"""
Starting a fan-out without a beat schedule. A notice is written by a trigger, which no Python sees - so in a
project whose beat doesn't run `fan_out` (no beat at all, tasks run inline, or a worker alone), something
has to notice that one is waiting:

- `FanOutAfterWritesMiddleware` - after every request that could have written (POST, PUT, PATCH, DELETE),
  once its transaction has committed.
- `FAN_OUT_AFTER_TASKS` - after every Celery task but isik's own.
- `manage.py notices --work` - for a cron: fan-out, the reaper and both prunes, what a beat would run.

The first two ask `fan_out_if_waiting()`, one read of the partial index on waiting notices, which is empty
but for a moment after a write - so a request or task that wrote none pays one indexed lookup.

`isik.W006` warns when isik sees nothing starting a fan-out, and `isik.W007` when tasks run inline and a
webhook would be sent inside the request or task that wrote its notice. Each is acknowledged with a reason:
`FAN_OUT_BY = ScheduledElsewhere(reason=...)`, `SENDS_INLINE = SendsInline(reason=...)` (from `exemptions`).
"""

from django.conf import settings

from isik.django.apps.notifications import conf
from isik.django.apps.notifications.swapping import get_notice_model


MIDDLEWARE = "isik.django.apps.notifications.hooks.FanOutAfterWritesMiddleware"
FAN_OUT_TASK = "isik.notifications.fan_out"


# The methods that can write; a GET or HEAD writes no notice worth starting a fan-out for.
WRITING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def fan_out_if_waiting():
    """Asks for a fan-out when a notice is waiting for one. Returns whether it asked."""
    from isik.django.apps.notifications import tasks

    if not get_notice_model()._default_manager.filter(fanned_out_at__isnull=True).exists():
        return False
    tasks.fan_out.delay()
    return True


def _celery_conf():
    from celery import current_app

    return current_app.conf


def started_after_writes():
    """Whether the middleware or `FAN_OUT_AFTER_TASKS` starts a fan-out after a write."""
    return MIDDLEWARE in settings.MIDDLEWARE or conf.setting("FAN_OUT_AFTER_TASKS")


def fan_out_scheduled():
    """Whether the Celery app's beat runs `fan_out` - `beat_schedule()` merged into it."""
    return any(entry["task"] == FAN_OUT_TASK for entry in (_celery_conf().beat_schedule or {}).values())


def runs_inline():
    """Whether Celery runs a task where it's asked for (`task_always_eager`)."""
    return bool(_celery_conf().task_always_eager)


class FanOutAfterWritesMiddleware:
    """
    After each request that could have written - its response made, its transaction (ATOMIC_REQUESTS)
    committed - starts a fan-out if a notice is waiting. Put it anywhere in MIDDLEWARE.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method in WRITING_METHODS:
            fan_out_if_waiting()
        return response


def fan_out_after_task(sender=None, **kwargs):
    """
    Celery's `task_postrun` receiver, always connected: while `FAN_OUT_AFTER_TASKS` is on, after any task but
    isik's own notifications tasks, `fan_out_if_waiting()`.
    """
    if conf.setting("FAN_OUT_AFTER_TASKS") and not sender.name.startswith("isik.notifications."):
        fan_out_if_waiting()
