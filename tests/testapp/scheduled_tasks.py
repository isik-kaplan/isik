"""The test project's scheduled tasks: a schedule for each of isik's that has none of isik's, and one of its own."""

from datetime import timedelta

from celery import shared_task

from isik.django.scheduling import StaffMay, override_scheduled_task, scheduled_task


@shared_task(name="testapp.cleanup")
def cleanup(days=30):
    return days


override_scheduled_task("isik.accounts.sweep_deletions", every=timedelta(hours=1))
override_scheduled_task("isik.inbound.work", every=timedelta(minutes=1))
scheduled_task(
    "testapp.cleanup",
    task="testapp.cleanup",
    crontab="0 3 * * *",
    kwargs={"days": 7},
    staff_may=StaffMay.VIEW | StaffMay.PAUSE | StaffMay.RUN_NOW | StaffMay.RESCHEDULE,
    source_editable=True,
)
