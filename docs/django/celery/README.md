# celery

`pip install isik[celery]`, which brings the `django` extra with it.

`HistoryContextTask` is a Celery task base whose history rows name whoever caused the task. A task
runs in a worker with no request, so otherwise every row it writes has an empty pghistory context,
including rows a person plainly caused.

```python
from isik.django.celery import HistoryContextTask

@shared_task(base=HistoryContextTask)
def provision(organization_id):
    ...
```

Requires [`HistoryContextMiddleware`](../apps/common/middleware/history.md), which is where
dispatch reads the request's context from.

- **On dispatch**, the cause goes into the message headers under `history_context_header`
  (`"isik_history_context"`). Headers rather than arguments, so no task signature changes and the
  cause stays out of the arguments a failure logs. A caller's own `headers=` are kept. If a caller
  sets `history_context_header` itself, its value wins.
- **In the worker**, the task body runs inside a `pghistory.context()` made of that cause plus
  `worker_history_context()`, which is `{"task": self.name}` unless overridden.
- **Only `carried_history_keys` travel**, `("user",)` by default. The request's `url` stays behind,
  because the task isn't that route. A carried key the request doesn't have is left out rather than
  sent as `None`.
- **`caused_by`** is `"request"` for a task dispatched while serving one and `"system"` otherwise
  (a beat schedule, a shell), because "nobody asked" and "we lost who asked" would otherwise be the
  same empty value. A message without the header runs as `"system"` too. That covers one sent
  before this was deployed, or by a producer that doesn't set it.
- **Run inside a request** (`task_always_eager`, or the task called directly), the body runs in that
  request's context untouched, because its rows are the request's. pghistory would otherwise fold
  the task's keys into the request's context for good, and every later row of that request would
  claim to be the task's.

A row a worker writes for a request then reads:

```json
{"user": 7, "caused_by": "request", "task": "organizations.provision"}
```

Carry more keys, or add what only the worker knows, by subclassing:

```python
class TenantTask(HistoryContextTask):
    carried_history_keys = ("user", "organization", "schema")

    def worker_history_context(self):
        # The build that runs a task wrote its rows, and a worker mid-deploy isn't the web process's.
        return {**super().worker_history_context(), "version": __version__}
```

The cause is read when `apply_async` runs. A base that defers dispatch to
`transaction.on_commit` still gets the request's cause, as long as the commit happens while the
request is being served. That's true of `ATOMIC_REQUESTS`.
