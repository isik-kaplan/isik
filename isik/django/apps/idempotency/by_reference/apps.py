from django.apps import AppConfig

from isik._internal.translation import gettext_lazy


class IdempotencyByReferenceConfig(AppConfig):
    name = "isik.django.apps.idempotency.by_reference"
    label = "idempotency_by_reference"
    verbose_name = gettext_lazy("Idempotency claims")
