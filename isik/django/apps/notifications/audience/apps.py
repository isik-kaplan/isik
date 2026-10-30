from django.apps import AppConfig

from isik._internal.translation import gettext_lazy
from isik.django.apps.notifications import swapping
from isik.django.apps.notifications.audience.swapping import set_defaults


# Before any model or migration is loaded, so a migration reading a swappable setting finds it - the
# ledger's too, which the audience's keys name.
set_defaults()
swapping.set_defaults()


class NotificationsAudienceConfig(AppConfig):
    name = "isik.django.apps.notifications.audience"
    label = "isik_notifications_audience"
    verbose_name = gettext_lazy("Notification audience")
