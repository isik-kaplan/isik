from django.apps import AppConfig

from isik._internal.translation import gettext_lazy
from isik.django.apps.sync.swapping import set_defaults


# Before any model or migration is loaded, so a migration reading the swappable setting finds it.
set_defaults()


class SyncConfig(AppConfig):
    name = "isik.django.apps.sync"
    label = "isik_sync"
    verbose_name = gettext_lazy("Sync")

    def ready(self):
        from isik.django.apps.sync import checks  # noqa: F401 - registers the checks
        from isik.django.apps.sync.declarations import log_relation_parents

        log_relation_parents()
