"""
The notifications' entries for a project's Celery beat schedule - isik schedules nothing itself. Nothing here
imports Django, so a project's `celery.py`, run before `django.setup()`, imports it at the top:

    from isik.django.apps.notifications.schedule import merge_beat_schedule

    app = Celery("project")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    merge_beat_schedule(app)

`merge_beat_schedule()` merges them once the app is configured - reading `app.conf` in `celery.py` itself
would load the settings while the project's package is still half imported.
"""

from datetime import timedelta


def beat_schedule(fan_out_every=timedelta(seconds=10), reap_every=timedelta(minutes=1), prune_every=timedelta(hours=1)):
    """The beat entries running the notifications - each task, every so often."""
    return {
        "isik.notifications.fan_out": {"task": "isik.notifications.fan_out", "schedule": fan_out_every},
        "isik.notifications.reap_stranded": {"task": "isik.notifications.reap_stranded", "schedule": reap_every},
        "isik.notifications.prune_attempts": {"task": "isik.notifications.prune_attempts", "schedule": prune_every},
        "isik.notifications.prune_notices": {"task": "isik.notifications.prune_notices", "schedule": prune_every},
    }


def merge_beat_schedule(app, **every):
    """
    Merges `beat_schedule(**every)` into the Celery `app`'s beat schedule once it's configured - its own
    entries kept beside them.
    """

    def merge(sender, **kwargs):
        sender.conf.beat_schedule = {**(sender.conf.beat_schedule or {}), **beat_schedule(**every)}

    # The signal holds its receivers weakly, and nothing else keeps this one - so the app does.
    app.isik_notifications_merge = merge
    app.on_after_configure.connect(merge)
