"""Settings for the swapped project - its own database, and every notifications and features model the project's."""

import getpass
import os

from isik.django.apps.notifications.exemptions import ScheduledElsewhere


SECRET_KEY = "not-a-secret-just-for-tests"
USE_TZ = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB_SWAPPED", "isik_swapped"),
        "USER": os.environ.get("POSTGRES_USER", getpass.getuser()),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", ""),
        "PORT": os.environ.get("POSTGRES_PORT", ""),
    }
}
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "pgtrigger",
    "pghistory",
    "isik.django.apps.common",
    "isik.django.apps.notifications",
    "isik.django.apps.notifications.audience",
    "isik.django.apps.features",
    "tests_swapped.owned",
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True}]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
# Every model isik ships is the project's own here, each named in ISIK - isik gives Django the top-level
# ISIK_NOTIFICATIONS_*_MODEL and ISIK_FEATURES_*_MODEL settings it swaps them by.
ISIK = {
    "DJANGO": {
        "NOTIFICATIONS": {
            "CHANNELS": ("inbox", "email", "webhook"),
            "PROJECT_NAME": "Owned",
            # Each endpoint is a user's, and hears only what that user would.
            "ENDPOINT_OWNER_FIELD": "owner",
            "FAN_OUT_BY": ScheduledElsewhere(
                reason="each test runs the fan-out it needs itself, with no beat or worker"
            ),
            "KIND_POLICY_MODEL": "owned.KindPolicy",
            "NOTICE_MODEL": "owned.Notice",
            "FAN_OUT_SHARD_MODEL": "owned.FanOutShard",
            "DELIVERY_MODEL": "owned.Delivery",
            "WEBHOOK_DELIVERY_MODEL": "owned.WebhookDelivery",
            "EMAIL_DELIVERY_MODEL": "owned.EmailDelivery",
            "INBOX_DELIVERY_MODEL": "owned.InboxDelivery",
            "PUSH_DELIVERY_MODEL": "owned.PushDelivery",
            "DELIVERY_ATTEMPT_MODEL": "owned.DeliveryAttempt",
            "SUBSCRIPTION_MODEL": "owned.Subscription",
            "INBOX_ITEM_MODEL": "owned.InboxItem",
            "ENDPOINT_MODEL": "owned.Endpoint",
            "ENDPOINT_KIND_MODEL": "owned.EndpointKind",
            "DEVICE_MODEL": "owned.Device",
        },
        "FEATURES": {
            "SETTINGS": "tests_swapped.owned.feature_settings",
            "GRANT_SOURCES": ("manual", "subscription"),
            "PEOPLE": "tests_swapped.owned.people.of",
            "HOLDER_MODEL": "owned.Organization",
            "FEATURE_MODEL": "owned.Feature",
            "GRANT_MODEL": "owned.FeatureGrant",
            "SWEEP_MODEL": "owned.SweepCursor",
            "OVERRIDES_MODEL": "owned.OrganizationOverrides",
        },
    },
}
