"""
The task state model's abstract base - `AbstractScheduledTaskState` - for a project to own the table: subclass it,
track its history, and name it in `ISIK['DJANGO']['SCHEDULING']['TASK_MODEL']`.

    @track_events()
    class ScheduledTaskState(AbstractScheduledTaskState):
        class Meta(AbstractScheduledTaskState.Meta):
            db_table = "scheduled_task_state"
"""

from django.db import models

from isik._internal.translation import gettext_lazy
from isik.django.apps.common.db.models import BaseModel
from isik.django.scheduling import Source


class AbstractScheduledTaskState(BaseModel):
    """
    What staff did to one declared scheduled task: whether it's paused, the schedule they gave it, and the restart
    mode they chose - each kept, or set back to the declaration's at a scheduler start, as its `on_restart` says.
    """

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text=gettext_lazy("The scheduled task's declared name."),
        db_comment="The declared scheduled task this row is the state of - one row per name.",
        verbose_name=gettext_lazy("name"),
    )
    paused = models.BooleanField(
        default=False,
        help_text=gettext_lazy("Whether the task is paused."),
        db_comment="Whether the scheduler leaves the task unrun - seeded from its declaration.",
        verbose_name=gettext_lazy("paused"),
    )
    every = models.DurationField(
        null=True,
        blank=True,
        help_text=gettext_lazy("How often staff have it run - none for the declaration's schedule."),
        db_comment="The interval staff gave the task. Null, with crontab null, for its declared schedule.",
        verbose_name=gettext_lazy("every"),
    )
    crontab = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=gettext_lazy("The crontab staff have it run at - none for the declaration's schedule."),
        db_comment="The crontab staff gave the task, five fields. Null, with every null, for its declared schedule.",
        verbose_name=gettext_lazy("crontab"),
    )
    on_restart = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        choices=[(source.value, source.value) for source in Source],
        help_text=gettext_lazy("The restart mode staff chose - none for the declaration's."),
        db_comment="code or database, as staff chose it where the declaration lets them; null for the declaration's.",
        verbose_name=gettext_lazy("on restart"),
    )
    run_by_hand_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When staff last ran it by hand."),
        db_comment="When staff last asked for the task to run now - its history says who.",
        verbose_name=gettext_lazy("run by hand at"),
    )

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=models.Q(every__isnull=True) | models.Q(crontab__isnull=True),
                name="%(app_label)s_%(class)s_one_schedule",
                violation_error_message=gettext_lazy("A task runs every so often or at a crontab, not both."),
            )
        ]
