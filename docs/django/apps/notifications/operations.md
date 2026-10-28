# Running it

## The tasks and the beat schedule

`isik.django.apps.notifications.tasks` holds the Celery tasks, each a `HistoryContextTask` named
`isik.notifications.*`. isik schedules nothing - merge its entries into your own beat:

```python
# celery.py
from isik.django.apps.notifications.schedule import merge_beat_schedule

app = Celery("project")
app.config_from_object("django.conf:settings", namespace="CELERY")
merge_beat_schedule(app, fan_out_every=timedelta(seconds=10))
```

It merges once the app is configured: reading `app.conf` in `celery.py` itself would load the settings
while the project's package is still half imported. `beat_schedule()` is the entries alone, for a schedule
built another way. Test that your app's schedule holds them - `isik.W006` catches a project that never
merged them, nothing one that lost them later.

| task | what it does |
|---|---|
| `fan_out` | takes up to `BATCH_SIZE` waiting notices (`SELECT ... FOR UPDATE SKIP LOCKED`, oldest first), writes each one's deliveries or shards and stamps it, in one transaction, then asks for the sends; starts another fan-out while a full batch keeps coming back |
| `fan_out_shard` | claims a shard and writes its partition's deliveries |
| `send` | claims a due delivery, sends it on its channel, records the attempt |
| `reap_stranded` | takes back what a dead worker left, and asks again for what's overdue |
| `prune_attempts`, `prune_notices` | delete a batch of attempts and finished notices past their retention; another batch while one comes back full |

### Without a beat

A notice is written by a trigger, which no Python sees, so with no beat running `fan_out` - tasks run
inline, or a worker alone - something has to look for one. The first two ask
`hooks.fan_out_if_waiting()`, one read of the partial index on waiting notices, so a request or task that
wrote nothing pays one indexed lookup:

- **`FanOutAfterWritesMiddleware`** (`isik.django.apps.notifications.hooks`), anywhere in `MIDDLEWARE`:
  after a POST, PUT, PATCH or DELETE has made its response - and its `ATOMIC_REQUESTS` transaction has
  committed - it starts a fan-out if a notice is waiting.
- **`FAN_OUT_AFTER_TASKS: True`**: the same after every Celery task but isik's own.
- **`manage.py notices --work`**, from a cron: what a beat would run, once - fan out everything waiting,
  `reap_stranded`, and both prunes until nothing's left past its retention. (`--fan-out` alone does only
  the first.)

**Without a beat, run `--work` from a cron too.** Nothing else retries a failed send or prunes: with
tasks run inline, a retry's `eta` is ignored, so its task runs at once, finds the delivery not yet due and
does nothing - the reaper sends it later, once it's overdue by `STRANDED_AFTER`. Retries land at the cron's
granularity plus that. And with no pruning, `DeliveryAttempt` and `Notice` grow forever.

**With tasks run inline** (`task_always_eager`), the middleware fans out and sends inside the request,
after the view and before the response goes out: a write pays for its email, and a webhook endpoint can
hold the request for up to `ENDPOINT_TIMEOUT`. That's what running tasks inline means; run a worker when
it isn't acceptable.

The checks hold both: **`isik.W006`** warns when isik sees nothing starting a fan-out - no middleware, no
`FAN_OUT_AFTER_TASKS`, and no `fan_out` in the Celery app's `beat_schedule`. A cron or django-celery-beat's
rows, which isik can't see, are said with a reason (the reasons' types are in
`isik.django.apps.notifications.exemptions`): `FAN_OUT_BY=ScheduledElsewhere(reason="a cron runs
notices --work every minute")`. **`isik.W007`** warns when tasks run inline, a fan-out follows each write
and some kind is sent by webhook; `SENDS_INLINE=SendsInline(reason=...)` says why that's fine.

| background jobs | what starts the work |
|---|---|
| a worker and a beat | `beat_schedule()` merged |
| a worker, no beat | the middleware, `FAN_OUT_AFTER_TASKS`, and a cron of `notices --work` with `FAN_OUT_BY` |
| inline | the middleware, `FAN_OUT_AFTER_TASKS`, and a cron of `notices --work` |

**The row is the queue; a task is only a hint.** A Celery message is acknowledged before it runs, so a
worker dying mid-task loses it - `reap_stranded` is what makes a lost task a late one. Run it.

## When things go wrong

- **A notice that can't be fanned out** is stamped and set aside as a shard, which is tried again on a
  backoff and abandoned after `MAX_ATTEMPTS` - so one bad notice never heads the queue every fan-out
  takes from, holding up everyone else's.
- **A failed delivery** waits `BACKOFF`, and after `MAX_ATTEMPTS` - or a permanent failure - it's
  abandoned.
- **A partition that's gone** abandons the shard, delivery or retry into it at once, with that reason -
  see [partitions.md](partitions.md).
- **A worker taken over.** Each claim is a timestamp the worker finishes against. A worker that ran past
  `STRANDED_AFTER` and was taken back finds its claim gone and drops its result, rather than writing over
  the worker that replaced it.
- **The reaper.** A delivery sending, or a shard claimed, for longer than `STRANDED_AFTER` goes back to
  waiting - or is abandoned, when its run has had `MAX_ATTEMPTS`, so one that kills its worker every time
  doesn't come back forever. Only what's overdue by `STRANDED_AFTER` - its task lost, not merely queued
  behind others - is asked for again, up to `HOUSEKEEPING_BATCH` at a time.
- **What can't be stored.** An attempt's request, response and error are stored without NUL characters,
  and in a savepoint: a record that can't be written loses the record alone, never the delivery's state.
- **Pruning** takes `HOUSEKEEPING_BATCH` at a time, never a notice still waiting or with work unfinished -
  retention can't swallow a backlog - and skips one a manual retry holds.

## Retrying by hand

An abandoned delivery can be sent again - a receiver fixed the bug that had it answering 400:

```python
from isik.django.apps.notifications.pipeline import RetryRefused, retry_delivery

retry_delivery(delivery)   # due now, a new run of MAX_ATTEMPTS, its next attempt recorded as "manual"
```

It's refused, with `RetryRefused` saying why, for a delivery that isn't abandoned, or whose channel can't
reach its target - a parked endpoint is unparked first, as its own decision (`endpoint.unpark()`), so a
retry of fifty deliveries never unparks one on the strength of the first. `attempt_count` keeps counting
across runs; `attempts_before_run` is where the current one began. `DeliveryViewSet` offers it over REST
- see [rest.md](rest.md).

## Health

`pipeline.oldest_unfanned_notice_age()` - how long the oldest notice has waited, or None. A fan-out that
stopped running raises no error anywhere; report this from your health check.

## `manage.py notices`

Lists every declared kind - its source, model, trigger, condition, variants, audience, policy, scope,
channels and snapshot - and where the declarations and the policy rows differ: declared with no row
(run `migrate`), a row no declaration makes, declared and retired. `--format json`, and `--retire
<kind>` (see [kinds.md](kinds.md)).

## Tests

`TRUNCATE` is refused on the notice and attempt tables, and a `transaction=True` test - or any
`TransactionTestCase` - ends by flushing every table with one. isik's pytest plugin lets that flush
through on its own, whenever the ledger app is installed: nothing to add to your `conftest.py`. Under
`unittest`, open `flushing_through_truncate_protection()` (from `isik.django.apps.common.db`) around the
run. `manage.py flush` in development meets the same refusal - run it inside `allow_truncate()`, or
drop the database instead. `ProtectTruncate` is there for your own append-only tables.

The pipeline's steps are plain functions in `pipeline.py` (`fan_out()`, `run_shard()`, `send()`,
`retry_delivery()`, `reap_stranded()`, ...), so a test runs them without a broker.
