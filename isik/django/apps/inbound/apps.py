from django.apps import AppConfig

from isik._internal.translation import gettext_lazy
from isik.django.apps.inbound.swapping import set_defaults


# Before any model or migration is loaded, so a migration reading the swappable setting finds it.
set_defaults()


class InboundConfig(AppConfig):
    name = "isik.django.apps.inbound"
    label = "isik_inbound"
    verbose_name = gettext_lazy("Inbound webhooks")

    def ready(self):
        from isik.django.apps.inbound import checks  # noqa: F401 - registers the checks
        from isik.django.apps.inbound.sources import import_sources_module

        import_sources_module()
