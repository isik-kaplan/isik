"""The accounts app's Celery task - declared as the scheduled task `isik.accounts.sweep_deletions`
(`isik.django.scheduling`), which the project gives a schedule."""

from isik._internal import check_extra


check_extra("celery", "celery")

from celery import shared_task  # noqa: E402

from isik.django.apps.accounts import deletion  # noqa: E402
from isik.django.celery.tasks import HistoryContextTask  # noqa: E402


@shared_task(base=HistoryContextTask, name="isik.accounts.sweep_deletions")
def sweep_deletions():
    return deletion.sweep_deletions()
