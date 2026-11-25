"""
The features app's Celery task - `sweep_boundaries`, the scheduled task `isik.features.sweep`
(`isik.django.scheduling`), every minute unless the project changes it.
"""

from isik._internal import check_extra


check_extra("celery", "celery")

from celery import shared_task  # noqa: E402

from isik.django.apps.features import operations  # noqa: E402
from isik.django.celery.tasks import HistoryContextTask  # noqa: E402


@shared_task(base=HistoryContextTask, name="isik.features.sweep")
def sweep_boundaries():
    """Tells what grants starting or ending since the last sweep moved."""
    return operations.sweep_boundaries()
