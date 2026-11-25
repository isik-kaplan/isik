"""
`isik.E031` - scheduled tasks that can't work: see `setting_problems()`, `declaration_problems()` and
`history_problems()`.
"""

from django.core.checks import Error, Tags

from isik._internal.translation import gettext as _
from isik.django.apps.common.checks import Check, isik_check
from isik.django.apps.scheduling import conf
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.scheduling import StaffMay, declared_tasks


AREA = "ISIK['DJANGO']['SCHEDULING']"


def setting_problems():
    """What's wrong with `ISIK['DJANGO']['SCHEDULING']`, one sentence each."""
    unknown = sorted(set(conf.configured()) - set(conf.KEYS))
    if unknown:
        return [_("%(area)s has keys it doesn't know: %(keys)s.") % {"area": AREA, "keys": ", ".join(unknown)}]
    return []


def declaration_problems():
    """Why a declared task can't run, or can't be what it says - one sentence each."""
    try:
        tasks = declared_tasks()
    except ValueError as refused:
        return [str(refused)]
    problems = []
    for task in tasks.values():
        if task.schedule is None:
            problems.append(
                _(
                    "%(name)s has no schedule - isik has none for it. Give it one: "
                    "override_scheduled_task(%(name)r, every=...) or crontab=."
                )
                % {"name": task.name}
            )
        if task.source_editable and not task.staff_may & (StaffMay.PAUSE | StaffMay.RESCHEDULE):
            problems.append(
                _(
                    "%(name)s lets staff switch its restart mode, and neither pause nor reschedule it - a mode keeping "
                    "what staff saved would keep nothing."
                )
                % {"name": task.name}
            )
    return problems


def history_problems():
    """Whether the task state model keeps a history - what says who paused, rescheduled or ran a task."""
    model = get_task_state_model()
    if getattr(model, "pgh_event_model", None) is None:
        return [
            _(
                "%(model)s has no history - it says who paused, rescheduled or ran a task. Track it with "
                "@track_events()."
            )
            % {"model": model._meta.label}
        ]
    return []


@isik_check(Check.SCHEDULING, Tags.models)
def scheduled_tasks_can_work(options, **kwargs):
    """Scheduled tasks that can't work - see the three above."""
    hint = _("See isik.django.apps.scheduling.")
    problems = setting_problems() + declaration_problems() + history_problems()
    return [Error(problem, hint=hint, id="isik.E031") for problem in problems]
