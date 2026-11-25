# scheduling

A scheduled task is declared once, in code: the Celery task it sends, its schedule, and what staff may do to it.
isik declares its own apps' tasks; a project declares its own and changes isik's.

- `isik.django.scheduling` - the declarations, and `merge_beat_schedule()` putting them in a Celery app's beat. It
  needs no database and imports no Django as it's imported, so a project's `celery.py` uses it before Django is set up.
- `isik.django.apps.scheduling` (`isik[scheduling]`) - staff controls: each task's state in a row of the project's
  own model, isik's beat scheduler running the tasks as declarations and rows say, and a REST API letting staff do to
  each what its declaration allows.

## Declaring

```python
# celery.py
from datetime import timedelta

from isik.django.scheduling import StaffMay, Source, merge_beat_schedule, override_scheduled_task, scheduled_task

app = Celery("project")
app.config_from_object("django.conf:settings", namespace="CELERY")

scheduled_task(
    "billing.renew",
    task="billing.tasks.renew",            # the Celery task it sends
    crontab="0 3 * * *",                   # or every=timedelta(...)
    kwargs={"grace": 3},                   # args=, kwargs=, options= - apply_async()'s
    staff_may=StaffMay.VIEW | StaffMay.PAUSE | StaffMay.RUN_NOW,
    on_restart=Source.CODE,                # the default
    source_editable=False,                 # the default
)
override_scheduled_task("isik.accounts.sweep_deletions", every=timedelta(hours=1))
merge_beat_schedule(app)
```

`ISIK["DJANGO"]["SCHEDULING"]["TASKS"]` may name a module declaring them instead - one importing no models - which isik
imports wherever the declarations are read.

- `staff_may` - what staff may do through the API: `VIEW`, `PAUSE` (and resume), `RUN_NOW`, `RESCHEDULE`. `VIEW |
  PAUSE` unless given.
- `on_restart` - what a task is after the scheduler starts: `Source.CODE`, its declared schedule and pause again;
  `Source.DATABASE`, what staff saved, the declaration seeding it once.
- `source_editable` - whether staff may switch `on_restart` themselves. False: the declaration's always rules.
- `paused` - whether it starts paused.

`override_scheduled_task(name, **changes)` changes any declared task - isik's own included - as `scheduled_task()`
takes it; a schedule given replaces the other kind.

isik's own, declared for each of its apps installed:

| task | schedule |
|---|---|
| `isik.notifications.fan_out` | every 10 seconds |
| `isik.notifications.reap_stranded` | every minute |
| `isik.notifications.prune_attempts`, `isik.notifications.prune_notices` | every hour |
| `isik.features.sweep` | every minute |
| `isik.accounts.sweep_deletions` | none - the project gives it one |
| `isik.inbound.work` | none - the project gives it one |

`merge_beat_schedule(app)` merges every declared task with a schedule into the app's beat schedule once it's
configured, the app's own entries kept beside them. A task with no schedule isn't merged - give it one.

## Staff controls

```python
INSTALLED_APPS = [..., "isik.django.apps.scheduling"]
CELERY_BEAT_SCHEDULER = "isik.django.apps.scheduling.scheduler:Scheduler"

router.register("scheduled-tasks", ScheduledTaskViewSet, basename="scheduled-task")   # isik.django.apps.scheduling.drf
```

With isik's scheduler the declarations are beat's schedule - `merge_beat_schedule()` merges nothing, and a beat
schedule of the Celery app's own beside it is refused at its start, as is a declaration naming a Celery task that
doesn't exist. Each task's row says what staff did to it: `paused`, the schedule they gave it (`every` or
`crontab`, both null for the declaration's), the restart mode they chose (`on_restart`, null for the
declaration's), and when they last ran it by hand. The scheduler reads the rows again every
`beat_max_loop_interval` (Celery's `CELERY_BEAT_MAX_LOOP_INTERVAL`), so what staff change reaches beat within it -
the project sets how soon.

Beat writes each task's last run to its row (`last_run_at`) as it sends it, and a scheduler start reads it back, so
a restart keeps every task's clock: an interval task runs an interval after its last run, not after the restart,
and one that came due while beat was down runs once at the start. `last_run_at` is a bookkeeping field of the row
(`BOOKKEEPING_FIELDS`, see [models](../common/db/models.md#bookkeeping-fields)) - writing it moves neither the
row's `ETag` nor its history.

`migrate`, and each scheduler start, reconcile the rows: every declared task gets its row, seeded from its
declaration, and a restart mode staff chose where the code no longer lets them (`source_editable` turned off) is
set back to the declaration's. At a scheduler start, a task in `CODE` mode gets its declared schedule and pause
back. Each change is logged at WARNING, and the rows' history records it under the context reason
`isik.scheduling.reconciled`.

`manage.py scheduled_tasks` lists each task as the scheduler runs it and what staff may do to it, and names the
rows of tasks no longer declared; `--prune` deletes those.

## The API

`ScheduledTaskViewSet`, staff only - DRF's `IsAdminUser` unless the project sets its own `permission_classes`:

| request | needs |
|---|---|
| `GET scheduled-tasks/`, `GET scheduled-tasks/{name}/` | `VIEW` - a task without it isn't listed |
| `POST {name}/pause/`, `POST {name}/resume/` | `PAUSE` |
| `POST {name}/run/` | `RUN_NOW` - sends the task now, as beat sends it |
| `PATCH {name}/` `{"every": seconds}` or `{"crontab": "m h dom mon dow"}` | `RESCHEDULE` |
| `PATCH {name}/` `{"on_restart": "code" \| "database"}` | `source_editable` |
| `POST {name}/reset/` | `PAUSE` or `RESCHEDULE` - the declared schedule and pause back now, as far as staff may change them |
| `POST {name}/reset-at-restart/` | `source_editable` - `on_restart` back to `code`, so the next start sets it back |

A `PATCH` asks `If-Match` the task's `ETag`; every write honors an `Idempotency-Key`. Something a task's
declaration doesn't allow is a 403, `not_allowed_by_declaration`, naming what it does allow.

Each task answers with:

```json
{
  "name": "billing.renew",
  "task": "billing.tasks.renew",
  "code": {"every": null, "crontab": "0 3 * * *", "paused": false, "on_restart": "code"},
  "saved": {"every": 3600, "crontab": null, "paused": false, "on_restart": null},
  "effective": {"every": 3600, "crontab": null, "paused": false, "on_restart": "code"},
  "may": ["view", "pause", "run_now"],
  "run_by_hand_at": null,
  "last_run_at": "2026-11-25T03:00:00.004Z"
}
```

`code` is what `reset/` sets back; `effective` is what the scheduler runs. `may` lists `switch_on_restart` for a
task whose declaration says `source_editable`.

## Owning the model

```python
@track_events(exclude=["last_run_at"])
class ScheduledTaskState(AbstractScheduledTaskState):       # isik.django.apps.scheduling.bases
    class Meta(AbstractScheduledTaskState.Meta):
        db_table = "scheduled_task_state"

ISIK = {"DJANGO": {"SCHEDULING": {"TASK_MODEL": "ops.ScheduledTaskState"}}}
```

Its history is what says who paused, rescheduled or ran a task - `isik.E031` asks for it, leaving out
`last_run_at`, which beat writes at every run.
