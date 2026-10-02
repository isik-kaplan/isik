from django.apps import AppConfig

from isik._internal.translation import gettext_lazy


class IdempotencyByReferenceConfig(AppConfig):
    name = "isik.django.apps.idempotency.by_reference"
    label = "idempotency_by_reference"
    verbose_name = gettext_lazy("Idempotency claims")
    # Fixed here rather than taken from DEFAULT_AUTO_FIELD, so the shipped migration matches every project.
    default_auto_field = "django.db.models.BigAutoField"
