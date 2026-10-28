from django.apps import AppConfig
from django.db.models.signals import post_migrate

from isik._internal.translation import gettext_lazy
from isik.django.apps.notifications.swapping import set_defaults


# Before any model or migration is loaded, so a key, or a migration, reading a swappable setting finds it.
set_defaults()


class NotificationsConfig(AppConfig):
    name = "isik.django.apps.notifications"
    label = "isik_notifications"
    verbose_name = gettext_lazy("Notifications")
    # Fixed here rather than taken from DEFAULT_AUTO_FIELD, so the shipped migration matches every project.
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from celery.signals import task_postrun

        from isik.django.apps.notifications import checks  # noqa: F401 - registers the checks
        from isik.django.apps.notifications.hooks import fan_out_after_task
        from isik.django.apps.notifications.sync import mirror_kinds

        post_migrate.connect(mirror_kinds, sender=self, dispatch_uid="isik_notifications_mirror_kinds")
        task_postrun.connect(fan_out_after_task)
