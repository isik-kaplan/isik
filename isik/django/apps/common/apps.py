import importlib

from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "isik.django.apps.common"

    modules_to_initialize = [
        "isik.django.apps.common.lookups",
    ]

    def ready(self):
        for module in self.modules_to_initialize:
            importlib.import_module(module)
