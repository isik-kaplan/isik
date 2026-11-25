"""
Scheduled tasks, each declared once in code: its Celery task, its schedule, and what staff may do to it. isik
declares its own apps' tasks; a project declares its own and changes isik's. Nothing here imports Django as it's
imported, so a project's `celery.py`, run before `django.setup()`, declares at the top:

    from isik.django.scheduling import merge_beat_schedule, override_scheduled_task, scheduled_task

    app = Celery("project")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    scheduled_task("billing.renew", task="billing.tasks.renew", crontab="0 3 * * *")
    override_scheduled_task("isik.accounts.sweep_deletions", every=timedelta(hours=1))
    merge_beat_schedule(app)

`merge_beat_schedule()` puts every declared task in the Celery app's beat schedule once it's configured. With the
scheduling app's scheduler (`isik.django.apps.scheduling`) the declarations are its schedule instead, and staff
change a task through its API as far as the task's `staff_may` allows.
"""

import enum
from dataclasses import dataclass, field, replace
from datetime import timedelta
from importlib import import_module

from isik._internal.translation import gettext as _


# The scheduling app's scheduler - CELERY_BEAT_SCHEDULER naming it makes the declarations beat's schedule.
SCHEDULER = "isik.django.apps.scheduling.scheduler:Scheduler"


class StaffMay(enum.Flag):
    """What staff may do to a scheduled task through the API - any of them together."""

    VIEW = enum.auto()
    PAUSE = enum.auto()
    RUN_NOW = enum.auto()
    RESCHEDULE = enum.auto()


class Source(enum.Enum):
    """
    What a task's schedule and pause are after the scheduler starts: `CODE`, the declaration's again; `DATABASE`,
    what staff saved, the declaration only seeding it once.
    """

    CODE = "code"
    DATABASE = "database"


# A crontab's five fields, in order.
CRONTAB_FIELDS = ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")


def crontab_fields(expression):
    """`{field: value}` of a crontab expression, `"m h dom mon dow"` - a ValueError for one without five fields."""
    values = expression.split()
    if len(values) != len(CRONTAB_FIELDS):
        raise ValueError(
            _("%(crontab)r isn't a crontab - it takes five fields: minute hour day-of-month month day-of-week.")
            % {"crontab": expression}
        )
    return {field: values[at] for at, field in enumerate(CRONTAB_FIELDS)}


def celery_schedule(every, crontab):
    """The Celery schedule running every `every` (a timedelta), or at `crontab` - None for neither."""
    if crontab is not None:
        from celery.schedules import crontab as celery_crontab

        return celery_crontab(**crontab_fields(crontab))
    return every


@dataclass(frozen=True)
class ScheduledTask:
    """One scheduled task, as declared. Made by `scheduled_task()`, changed by `override_scheduled_task()`."""

    name: str
    task: str
    every: timedelta | None = None
    crontab: str | None = None
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    staff_may: StaffMay = StaffMay.VIEW | StaffMay.PAUSE
    on_restart: Source = Source.CODE
    source_editable: bool = False
    paused: bool = False

    @property
    def schedule(self):
        """Its Celery schedule - None while it has none, which an isik task waits on its project for."""
        return celery_schedule(self.every, self.crontab)

    def beat_entry(self):
        """Its entry in a Celery beat schedule."""
        return {
            "task": self.task,
            "schedule": self.schedule,
            "args": self.args,
            "kwargs": self.kwargs,
            "options": self.options,
        }


def _checked_schedule(every, crontab):
    if every is not None and crontab is not None:
        raise TypeError(_("A scheduled task runs every so often or at a crontab - give every= or crontab=, not both."))
    if crontab is not None:
        crontab_fields(crontab)


def _isik_task(name, every=None):
    return ScheduledTask(name, name, every=every)


# isik's own apps' tasks, by app - each declared when its app is installed. A task with no schedule is one isik has
# no default for: the project gives it one, with override_scheduled_task().
ISIK_TASKS = {
    "isik.django.apps.notifications": (
        _isik_task("isik.notifications.fan_out", timedelta(seconds=10)),
        _isik_task("isik.notifications.reap_stranded", timedelta(minutes=1)),
        _isik_task("isik.notifications.prune_attempts", timedelta(hours=1)),
        _isik_task("isik.notifications.prune_notices", timedelta(hours=1)),
    ),
    "isik.django.apps.features": (_isik_task("isik.features.sweep", timedelta(minutes=1)),),
    "isik.django.apps.accounts": (_isik_task("isik.accounts.sweep_deletions"),),
    "isik.django.apps.inbound": (_isik_task("isik.inbound.work"),),
}

# The project's own declarations, and its changes to any task, by name - in the order made.
declared = {}
overrides = {}


def scheduled_task(name, *, task, **options):
    """
    Declares the scheduled task `name`, running the Celery task named `task` - every `every` (a timedelta) or at
    `crontab` (`"m h dom mon dow"`), with `args`, `kwargs` and `options` (`apply_async()`'s). Also:

    - `staff_may` - a `StaffMay`, what staff may do to it through the API: `VIEW | PAUSE` unless given.
    - `on_restart` - a `Source`: `CODE` (the default), the declaration's schedule and pause again at each scheduler
      start; `DATABASE`, what staff saved kept.
    - `source_editable` - whether staff may switch `on_restart` through the API. False unless given.
    - `paused` - whether it starts paused. False unless given.
    """
    if name in declared:
        raise ValueError(_("A scheduled task called %(name)s is declared already.") % {"name": name})
    _checked_schedule(options.get("every"), options.get("crontab"))
    if options.get("every") is None and options.get("crontab") is None:
        raise TypeError(_("%(name)s needs a schedule - every= or crontab=.") % {"name": name})
    declared[name] = ScheduledTask(name, task, **options)
    return declared[name]


def override_scheduled_task(name, **changes):
    """
    Changes the scheduled task `name` - isik's own, say - as `scheduled_task()` declares it: its schedule, what staff
    may do, `on_restart`. A schedule given replaces the other kind - `crontab=` drops an `every`.
    """
    _checked_schedule(changes.get("every"), changes.get("crontab"))
    if "every" in changes or "crontab" in changes:
        changes = {"every": None, "crontab": None, **changes}
    overrides[name] = {**overrides.get(name, {}), **changes}


def import_tasks_module():
    """Imports the module `ISIK['DJANGO']['SCHEDULING']['TASKS']` names, declaring the project's tasks there."""
    from isik.django.conf import django_setting

    module = django_setting("SCHEDULING", "TASKS")
    if module:
        import_module(module)


def declared_tasks(installed=None):
    """
    `{name: ScheduledTask}` - isik's own for each of its apps in `installed` (`INSTALLED_APPS`, unless given), then
    the project's, each changed as `override_scheduled_task()` said. A project declaring a name isik's is, or
    changing one nothing declares, is a ValueError.
    """
    from django.conf import settings

    import_tasks_module()
    installed = settings.INSTALLED_APPS if installed is None else installed
    tasks = {each.name: each for app, own in ISIK_TASKS.items() if app in installed for each in own}
    for name, task in declared.items():
        if name in tasks:
            raise ValueError(
                _("%(name)s is isik's own scheduled task - change it with override_scheduled_task().") % {"name": name}
            )
        tasks[name] = task
    for name, changes in overrides.items():
        if name not in tasks:
            raise ValueError(_("No scheduled task called %(name)s is declared to change.") % {"name": name})
        tasks[name] = replace(tasks[name], **changes)
    return tasks


def beat_schedule():
    """Each declared task with a schedule, as a Celery beat entry."""
    return {name: task.beat_entry() for name, task in declared_tasks().items() if task.schedule is not None}


def uses_isiks_scheduler(conf):
    """Whether the Celery app configured as `conf` runs isik's scheduler, so the declarations are beat's schedule."""
    return conf.beat_scheduler == SCHEDULER


def merge_beat_schedule(app):
    """
    Merges `beat_schedule()` into the Celery `app`'s beat schedule once it's configured, its own entries kept beside
    them - unless it runs isik's scheduler, whose schedule the declarations are already.
    """

    def merge(sender, **kwargs):
        if not uses_isiks_scheduler(sender.conf):
            sender.conf.beat_schedule = {**(sender.conf.beat_schedule or {}), **beat_schedule()}

    # The signal holds its receivers weakly, and nothing else keeps this one - so the app does.
    app.isik_scheduling_merge = merge
    app.on_after_configure.connect(merge)


def beat_runs(task, conf):
    """Whether the Celery app configured as `conf` runs `task` on beat - in its beat schedule, or declared there."""
    if any(entry["task"] == task for entry in (conf.beat_schedule or {}).values()):
        return True
    return uses_isiks_scheduler(conf) and any(
        each.task == task and each.schedule is not None for each in declared_tasks().values()
    )
