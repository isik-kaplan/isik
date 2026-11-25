from django.apps import AppConfig
from django.db.models.signals import post_migrate

from isik._internal.translation import gettext_lazy
from isik.django.apps.scheduling.swapping import set_defaults


# Before any model or migration is loaded, so a migration reading the swappable setting finds it.
set_defaults()


def reconcile_after_migrating(sender, **kwargs):
    """`migrate` gives every declared task its row, and sets back what the code no longer lets staff choose."""
    from isik.django.apps.scheduling.state import reconcile

    reconcile(restart=False)


class SchedulingConfig(AppConfig):
    name = "isik.django.apps.scheduling"
    label = "isik_scheduling"
    verbose_name = gettext_lazy("Scheduled tasks")

    def ready(self):
        from isik.django.apps.scheduling import checks  # noqa: F401 - registers the checks
        from isik.django.scheduling import import_tasks_module

        import_tasks_module()
        post_migrate.connect(reconcile_after_migrating, sender=self, dispatch_uid="isik_scheduling_reconcile")
