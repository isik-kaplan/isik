"""
A declared task's state - its declaration and its row together - and what staff may do to it:

    pause(name) / resume(name)          StaffMay.PAUSE
    run_now(name)                       StaffMay.RUN_NOW
    reschedule(name, every=... | crontab=...)   StaffMay.RESCHEDULE
    set_on_restart(name, Source.DATABASE)       source_editable
    reset(name)                         back to the code's now: the schedule (RESCHEDULE) and pause (PAUSE)
    reset_at_restart(name)              on_restart CODE, so the next scheduler start does it (source_editable)

Each refuses what the task's declaration doesn't allow with `NotAllowed`, and a name nothing declares with
`UnknownTask`. `reconcile()` is what `migrate` and each scheduler start run: every declared task gets its row, a
restart mode the code no longer lets staff choose is set back to the declaration's - loudly - and, at a start, a
task in `CODE` mode gets its declared schedule and pause back.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

import pghistory
from django.db import transaction
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.scheduling import Source, StaffMay, celery_schedule, crontab_fields, declared_tasks


logger = logging.getLogger(__name__)

# What a row's history says made a change no person asked for - at migrate, or at a scheduler start.
RECONCILED = "isik.scheduling.reconciled"


class NotAllowed(Exception):
    """Something a task's declaration doesn't let staff do."""

    code = "not_allowed_by_declaration"


class UnknownTask(LookupError):
    """A name no scheduled task is declared under."""


@dataclass(frozen=True)
class State:
    """A task's schedule, pause and restart mode - as declared, as saved, or as the scheduler runs it."""

    every: timedelta | None
    crontab: str | None
    paused: bool
    on_restart: Source

    @property
    def schedule(self):
        return celery_schedule(self.every, self.crontab)


def declared_state(task):
    """What `task`'s declaration says."""
    return State(task.every, task.crontab, task.paused, task.on_restart)


def on_restart_of(task, row):
    """`task`'s restart mode: the one staff chose where its declaration lets them, else the declaration's."""
    if task.source_editable and row is not None and row.on_restart is not None:
        return Source(row.on_restart)
    return task.on_restart


def effective_state(task, row):
    """What the scheduler runs `task` as: the schedule staff gave it, else the declaration's, and its row's pause."""
    if row is None:
        return declared_state(task)
    given = row.every is not None or row.crontab is not None
    every, crontab = (row.every, row.crontab) if given else (task.every, task.crontab)
    return State(every, crontab, row.paused, on_restart_of(task, row))


def rows():
    """Every task state row, by name."""
    return {row.name: row for row in get_task_state_model()._default_manager.all()}


def _reconciled(task, row, restart):
    """What reconciling changes in `row`, as sentences - each change made to `row`, unsaved."""
    changes = []
    if row.on_restart is not None and not task.source_editable:
        changes.append(
            _("on_restart %(saved)s set back to the declaration's %(declared)s - the code no longer lets staff choose")
            % {"saved": row.on_restart, "declared": task.on_restart.value}
        )
        row.on_restart = None
    code = (None, None, task.paused)
    if restart and on_restart_of(task, row) is Source.CODE and (row.every, row.crontab, row.paused) != code:
        changes.append(_("schedule and pause set back to the code's at the scheduler's start"))
        row.every, row.crontab, row.paused = code
    return changes


def reconcile(*, restart):
    """
    Gives every declared task its row, seeded from its declaration, and sets back what the code no longer allows -
    and at a scheduler start (`restart`), a `CODE` task's schedule and pause. Logs each change, which the rows'
    history records too. Returns `{name: [change, ...]}`.
    """
    model = get_task_state_model()
    changed = {}
    with transaction.atomic(), pghistory.context(reason=RECONCILED):
        saved = rows()
        for name, task in declared_tasks().items():
            row = saved.get(name)
            if row is None:
                model._default_manager.create(name=name, paused=task.paused)
                continue
            changes = _reconciled(task, row, restart)
            if changes:
                row.save()
                changed[name] = changes
                for change in changes:
                    logger.warning("%s: %s", name, change)
    return changed


def task_of(name):
    """The task declared as `name` - `UnknownTask` for none."""
    tasks = declared_tasks()
    if name not in tasks:
        raise UnknownTask(_("No scheduled task called %(name)s is declared.") % {"name": name})
    return tasks[name]


def may(task):
    """What staff may do to `task`, as the names the API lists - `source_editable` among them."""
    allowed = [member.name.lower() for member in StaffMay if member in task.staff_may]
    return [*allowed, "switch_on_restart"] if task.source_editable else allowed


def _refused(task, wanted):
    return NotAllowed(
        _("%(name)s's declaration doesn't let staff %(wanted)s it - it allows: %(allowed)s.")
        % {"name": task.name, "wanted": wanted, "allowed": ", ".join(may(task)) or _("nothing")}
    )


def _require(task, needed, wanted):
    if not task.staff_may & needed:
        raise _refused(task, wanted)


def _locked_row(task):
    row, _created = (
        get_task_state_model()
        ._default_manager.select_for_update()
        .get_or_create(name=task.name, defaults={"paused": task.paused})
    )
    return row


def _changed(name, needed, wanted, change):
    task = task_of(name)
    _require(task, needed, wanted)
    with transaction.atomic():
        row = _locked_row(task)
        change(task, row)
        row.save()
    return row


def _set_paused(paused):
    def change(task, row):
        row.paused = paused

    return change


def pause(name):
    """Pauses the task `name` - StaffMay.PAUSE."""
    return _changed(name, StaffMay.PAUSE, _("pause"), _set_paused(True))


def resume(name):
    """Resumes the task `name` - StaffMay.PAUSE."""
    return _changed(name, StaffMay.PAUSE, _("resume"), _set_paused(False))


def reschedule(name, *, every=None, crontab=None):
    """Runs the task `name` every `every`, or at `crontab`, from now on - StaffMay.RESCHEDULE."""
    if (every is None) == (crontab is None):
        raise ValueError(_("Give every= or crontab=, one of them."))
    if crontab is not None:
        crontab_fields(crontab)

    def change(task, row):
        row.every, row.crontab = every, crontab

    return _changed(name, StaffMay.RESCHEDULE, _("reschedule"), change)


def set_on_restart(name, source):
    """Switches the task `name`'s restart mode to `source` - where its declaration says `source_editable`."""
    task = task_of(name)
    if not task.source_editable:
        raise _refused(task, _("switch the restart mode of"))
    with transaction.atomic():
        row = _locked_row(task)
        row.on_restart = source.value
        row.save()
    return row


def reset(name):
    """Sets the task `name` back to the code's now - its schedule where RESCHEDULE is allowed, its pause where PAUSE."""
    task = task_of(name)
    _require(task, StaffMay.PAUSE | StaffMay.RESCHEDULE, _("reset"))
    with transaction.atomic():
        row = _locked_row(task)
        if StaffMay.RESCHEDULE in task.staff_may:
            row.every, row.crontab = None, None
        if StaffMay.PAUSE in task.staff_may:
            row.paused = task.paused
        row.save()
    return row


def reset_at_restart(name):
    """Has the next scheduler start set the task `name` back to the code's - its restart mode `CODE`."""
    return set_on_restart(name, Source.CODE)


def run_now(name):
    """Sends the task `name` now, as beat sends it - StaffMay.RUN_NOW. Its row says when, and its history who."""
    from celery import current_app

    def change(task, row):
        row.run_by_hand_at = timezone.now()
        transaction.on_commit(
            lambda: current_app.send_task(task.task, args=list(task.args), kwargs=task.kwargs, **task.options)
        )

    return _changed(name, StaffMay.RUN_NOW, _("run"), change)
