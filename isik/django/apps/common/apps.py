import importlib

from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "isik.django.apps.common"

    modules_to_initialize = [
        "isik.django.apps.common.db.lookups",
        # Imported for its @register side effect; a check has to be reachable by autodiscovery.
        "isik.django.apps.common.checks",
    ]

    def ready(self):
        from isik.django.apps.common.db.constraints import install

        for module in self.modules_to_initialize:
            importlib.import_module(module)
        # Last, so every model is final - including one that builds its own columns from a
        # `class_prepared` receiver of its own.
        install()
