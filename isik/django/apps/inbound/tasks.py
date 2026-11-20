"""
The inbound app's Celery tasks, each a thin call into `pipeline.py`. `beat_schedule()` names `work` for a project's
beat.
"""

from isik._internal import check_extra


check_extra("inbound", "celery")

from celery import shared_task  # noqa: E402

from isik.django.apps.inbound import pipeline  # noqa: E402
from isik.django.celery.tasks import HistoryContextTask  # noqa: E402


@shared_task(base=HistoryContextTask, name="isik.inbound.process")
def process(partition, event_id):
    event = pipeline.process(partition, event_id)
    return event.state if event is not None else None


@shared_task(base=HistoryContextTask, name="isik.inbound.work")
def work():
    return pipeline.work()


def beat_schedule(every):
    """The beat entry running `work` every `every` seconds - for a project to merge into its own schedule."""
    return {"isik.inbound.work": {"task": "isik.inbound.work", "schedule": every}}
