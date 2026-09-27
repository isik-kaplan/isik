import pghistory
from celery import Task

from isik.django.apps.common.db.history import open_history_context


class HistoryContextTask(Task):
    """
    A Celery task base whose rows name whoever caused them. A task runs in a worker with no request,
    so without this every row it writes has an empty pghistory context - including rows a person
    plainly caused.

        @shared_task(base=HistoryContextTask)
        def provision(organization_id): ...

    On dispatch, the cause is read from `open_history_context()` (so `HistoryContextMiddleware` has
    to be installed) and put in the message headers. In the worker, the task body runs inside a
    `pghistory.context()` holding that cause plus `worker_history_context()`. Headers rather than
    arguments, so no task signature changes and the cause stays out of the arguments a failure logs.

    - Only `carried_history_keys` travel. The request's `url` is not the task, and copying it would
      claim something false. Add your own `get_context()` keys here as they apply.
    - `caused_by` is `"request"` for a task dispatched while serving one, and `"system"` otherwise
      (a beat schedule, a shell), because "nobody asked" and "we lost who asked" would otherwise be
      the same empty value. A message without the header, sent before this was deployed or by a
      producer that doesn't set it, runs as `"system"` too.
    - A caller's own `headers=` are kept, and a caller setting `history_context_header` itself wins.
    - Run inside a request (`task_always_eager`, or the task called directly), the body runs in that
      request's context untouched: its rows are the request's.
    """

    history_context_header = "isik_history_context"
    carried_history_keys = ("user",)

    def history_cause(self):
        """What the message carries of who asked for it - read when the task is dispatched."""
        opened = open_history_context()
        if opened is None:
            return {"caused_by": "system"}
        return {**{key: opened[key] for key in self.carried_history_keys if key in opened}, "caused_by": "request"}

    def worker_history_context(self):
        """
        Added to the context in the worker, over the cause. The task's name by default, because "a
        person did this" and "a worker did this because a person asked" are different claims.
        Override to add what only the worker knows, such as the build that is running the task.
        """
        return {"task": self.name}

    def apply_async(self, args=None, kwargs=None, **options):
        options["headers"] = {self.history_context_header: self.history_cause(), **(options.get("headers") or {})}
        return super().apply_async(args, kwargs, **options)

    def __call__(self, *args, **kwargs):
        if open_history_context() is not None:
            # Running inside a request - eagerly, or called directly - so the rows it writes are the
            # request's, and already name its cause. pghistory would fold a context opened here into
            # the request's and never take it back out, leaving every later row of that request
            # claiming to be this task's.
            return super().__call__(*args, **kwargs)
        headers = self.request.headers or {}
        cause = headers.get(self.history_context_header) or {"caused_by": "system"}
        with pghistory.context(**{**cause, **self.worker_history_context()}):
            return super().__call__(*args, **kwargs)
