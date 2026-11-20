"""The accounts app's Celery task - `beat_schedule()` names it for a project's beat."""

from isik._internal import check_extra


check_extra("celery", "celery")

from celery import shared_task  # noqa: E402

from isik.django.apps.accounts import deletion  # noqa: E402
from isik.django.celery.tasks import HistoryContextTask  # noqa: E402


@shared_task(base=HistoryContextTask, name="isik.accounts.sweep_deletions")
def sweep_deletions():
    return deletion.sweep_deletions()


def beat_schedule(every):
    """The beat entry running `sweep_deletions` every `every` seconds - for a project to merge into its own."""
    return {"isik.accounts.sweep_deletions": {"task": "isik.accounts.sweep_deletions", "schedule": every}}
