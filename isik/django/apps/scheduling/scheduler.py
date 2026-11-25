"""
isik's beat scheduler - `CELERY_BEAT_SCHEDULER = "isik.django.apps.scheduling.scheduler:Scheduler"`. Its schedule is
the declared tasks, each as its row says - paused ones left out - read again every `beat_max_loop_interval`, so what
staff change reaches beat within it. At its start it reconciles the rows (`state.reconcile(restart=True)`), and
refuses a beat schedule of the Celery app's own beside the declarations, and a declaration naming a task Celery
doesn't have.
"""

import time

from isik._internal import check_extra


check_extra("scheduling", "celery")

from celery import beat  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402
from django.db import close_old_connections  # noqa: E402

from isik._internal.translation import gettext as _  # noqa: E402
from isik.django.apps.scheduling import state  # noqa: E402
from isik.django.scheduling import declared_tasks  # noqa: E402


class Scheduler(beat.Scheduler):
    def setup_schedule(self):
        self._refuse_what_cant_run()
        state.reconcile(restart=True)
        self.data = {}
        self._read()

    def _refuse_what_cant_run(self):
        if self.app.conf.beat_schedule:
            raise ImproperlyConfigured(
                _(
                    "The Celery app has a beat schedule of its own beside isik's scheduler: %(names)s - declare each "
                    "with scheduled_task()."
                )
                % {"names": ", ".join(sorted(self.app.conf.beat_schedule))}
            )
        self.app.loader.import_default_modules()
        unknown = sorted(task.task for task in declared_tasks().values() if task.task not in self.app.tasks)
        if unknown:
            raise ImproperlyConfigured(
                _("Scheduled tasks name Celery tasks that don't exist: %(tasks)s.") % {"tasks": ", ".join(unknown)}
            )

    def _read(self):
        """The schedule, as the declarations and the rows say now - each entry's last run kept."""
        close_old_connections()
        saved = state.rows()
        entries = {}
        for name, task in declared_tasks().items():
            current = state.effective_state(task, saved.get(name))
            if current.paused or current.schedule is None:
                continue
            previous = self.data.get(name)
            entries[name] = self.Entry(
                name=name,
                task=task.task,
                schedule=current.schedule,
                args=task.args,
                kwargs=task.kwargs,
                options=task.options,
                last_run_at=previous.last_run_at if previous is not None else None,
                app=self.app,
            )
        self.data = entries
        self._read_at = time.monotonic()

    @property
    def schedule(self):
        if time.monotonic() - self._read_at >= self.max_interval:
            self._read()
        return self.data
