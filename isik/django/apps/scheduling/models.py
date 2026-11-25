"""The task state model isik ships, `ScheduledTaskState` - swapped out by `ISIK['DJANGO']['SCHEDULING']['TASK_MODEL']`
for a project owning it, see `bases.py`."""

from isik._internal.translation import gettext_lazy
from isik.django.apps.common.db.history import history_swapped_with, track_events
from isik.django.apps.scheduling.bases import AbstractScheduledTaskState
from isik.django.apps.scheduling.swapping import TASK_HISTORY_MODEL, TASK_MODEL


# Its history says who paused, rescheduled or ran a task, and when - swapped out with it.
@track_events(meta={"swappable": TASK_HISTORY_MODEL})
class ScheduledTaskState(AbstractScheduledTaskState):
    class Meta(AbstractScheduledTaskState.Meta):
        db_table = "isik_scheduled_task_state"
        swappable = TASK_MODEL
        verbose_name = gettext_lazy("scheduled task state")
        verbose_name_plural = gettext_lazy("scheduled task states")


history_swapped_with(ScheduledTaskState, TASK_MODEL)
