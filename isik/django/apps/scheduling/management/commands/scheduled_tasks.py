from django.core.management.base import BaseCommand

from isik._internal.translation import gettext as _
from isik.common.utils.exemption_listing import columns
from isik.django.apps.scheduling import state
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.scheduling import declared_tasks


def described(state_):
    """A state's schedule and pause, as a cell."""
    schedule = state_.crontab if state_.crontab is not None else str(state_.every)
    return _("%(schedule)s, paused") % {"schedule": schedule} if state_.paused else schedule


class Command(BaseCommand):
    help = (
        "Lists every declared scheduled task - what the scheduler runs it as, what staff may do to it - and the rows "
        "of tasks no longer declared. --prune deletes those rows."
    )

    def add_arguments(self, parser):
        parser.add_argument("--prune", action="store_true", help="Delete the rows of tasks no longer declared.")

    def handle(self, *args, prune, **options):
        tasks, saved = declared_tasks(), state.rows()
        rows = [
            (name, described(state.effective_state(task, saved.get(name))), ", ".join(state.may(task)))
            for name, task in tasks.items()
        ]
        if rows:
            self.stdout.write("\n".join(columns(rows)))
        stale = sorted(set(saved) - set(tasks))
        if prune:
            get_task_state_model()._default_manager.filter(name__in=stale).delete()
            for name in stale:
                self.stdout.write(_("Deleted the row of %(name)s, which nothing declares.") % {"name": name})
            return
        for name in stale:
            self.stderr.write(_("%(name)s has a row and no declaration - --prune deletes it.") % {"name": name})
