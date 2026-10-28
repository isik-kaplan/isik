"""
The notifications' Celery tasks, each a thin call into `pipeline.py`. `schedule.beat_schedule()` names them
for a project's beat.
"""

from isik._internal import check_extra


check_extra("notifications", "celery")

from celery import shared_task  # noqa: E402

from isik.django.apps.notifications import conf, pipeline  # noqa: E402
from isik.django.celery.tasks import HistoryContextTask  # noqa: E402


@shared_task(base=HistoryContextTask, name="isik.notifications.fan_out")
def fan_out():
    """Fans out a batch of waiting notices - and starts another fan-out while a full batch keeps coming back."""
    fanned = pipeline.fan_out()
    if fanned >= conf.setting("BATCH_SIZE"):
        fan_out.delay()
    return fanned


@shared_task(base=HistoryContextTask, name="isik.notifications.fan_out_shard", bind=True)
def fan_out_shard(self, shard_id):
    shard = pipeline.run_shard(shard_id, claimed_by=self.request.id or "")
    return shard.state if shard is not None else None


@shared_task(base=HistoryContextTask, name="isik.notifications.send")
def send(delivery_id, triggered_by="send"):
    delivery = pipeline.send(delivery_id, triggered_by=triggered_by)
    return delivery.state if delivery is not None else None


@shared_task(base=HistoryContextTask, name="isik.notifications.reap_stranded")
def reap_stranded():
    return pipeline.reap_stranded()


@shared_task(base=HistoryContextTask, name="isik.notifications.prune_attempts")
def prune_attempts():
    """Prunes a batch of attempts - and starts another while a full batch keeps coming back."""
    pruned = pipeline.prune_attempts()
    if pruned >= conf.setting("HOUSEKEEPING_BATCH"):
        prune_attempts.delay()
    return pruned


@shared_task(base=HistoryContextTask, name="isik.notifications.prune_notices")
def prune_notices():
    """Prunes a batch of notices - and starts another while a full batch keeps coming back."""
    pruned = pipeline.prune_notices()
    if pruned >= conf.setting("HOUSEKEEPING_BATCH"):
        prune_notices.delay()
    return pruned
