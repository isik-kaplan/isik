from django.apps import AppConfig

from isik._internal.translation import gettext_lazy


class IdempotencyWithBodyConfig(AppConfig):
    name = "isik.django.apps.idempotency.with_body"
    label = "idempotency_with_body"
    verbose_name = gettext_lazy("Idempotency claims with bodies")
    # Fixed here rather than taken from DEFAULT_AUTO_FIELD, so the shipped migration matches every project.
    default_auto_field = "django.db.models.BigAutoField"
