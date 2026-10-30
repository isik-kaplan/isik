from django.apps import AppConfig
from django.db.models.signals import post_migrate, pre_delete

from isik._internal.translation import gettext_lazy
from isik.django.apps.features.swapping import get_holder_model, set_defaults


# Before any model or migration is loaded, so a key, or a migration, reading a swappable setting finds it.
set_defaults()


class FeaturesConfig(AppConfig):
    name = "isik.django.apps.features"
    label = "isik_features"
    verbose_name = gettext_lazy("Features")

    def ready(self):
        from isik.django.apps.features import checks  # noqa: F401 - registers the checks
        from isik.django.apps.features.operations import erase_with_holder
        from isik.django.apps.features.resolution import resolve_on_holder
        from isik.django.apps.features.sync import mirror_features

        post_migrate.connect(mirror_features, sender=self, dispatch_uid="isik_features_mirror")
        pre_delete.connect(erase_with_holder, sender=get_holder_model(), dispatch_uid="isik_features_erase")
        resolve_on_holder()
