"""
The features app's entry for a project's Celery beat schedule - isik schedules nothing itself. Nothing here imports
Django, so a project's `celery.py`, run before `django.setup()`, imports it at the top:

    from isik.django.apps.features.schedule import merge_beat_schedule

    app = Celery("project")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    merge_beat_schedule(app)

`merge_beat_schedule()` merges it once the app is configured - reading `app.conf` in `celery.py` itself would load
the settings while the project's package is still half imported.
"""

from datetime import timedelta


def beat_schedule(sweep_every=timedelta(minutes=1)):
    """The beat entry running the boundary sweep, every so often."""
    return {"isik.features.sweep": {"task": "isik.features.sweep", "schedule": sweep_every}}


def merge_beat_schedule(app, **every):
    """
    Merges `beat_schedule(**every)` into the Celery `app`'s beat schedule once it's configured - its own entries kept
    beside it.
    """

    def merge(sender, **kwargs):
        sender.conf.beat_schedule = {**(sender.conf.beat_schedule or {}), **beat_schedule(**every)}

    # The signal holds its receivers weakly, and nothing else keeps this one - so the app does.
    app.isik_features_merge = merge
    app.on_after_configure.connect(merge)
