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
ISIK_NOTIFICATIONS = {
    "CHANNELS": ("inbox", "email", "webhook"),
    "PROJECT_NAME": "Owned",
    # Each endpoint is a user's, and hears only what that user would.
    "ENDPOINT_OWNER_FIELD": "owner",
    "FAN_OUT_BY": ScheduledElsewhere(reason="each test runs the fan-out it needs itself, with no beat or worker"),
}

ISIK_NOTIFICATIONS_KIND_POLICY_MODEL = "owned.KindPolicy"
ISIK_NOTIFICATIONS_NOTICE_MODEL = "owned.Notice"
ISIK_NOTIFICATIONS_FAN_OUT_SHARD_MODEL = "owned.FanOutShard"
ISIK_NOTIFICATIONS_DELIVERY_MODEL = "owned.Delivery"
ISIK_NOTIFICATIONS_WEBHOOK_DELIVERY_MODEL = "owned.WebhookDelivery"
ISIK_NOTIFICATIONS_EMAIL_DELIVERY_MODEL = "owned.EmailDelivery"
ISIK_NOTIFICATIONS_INBOX_DELIVERY_MODEL = "owned.InboxDelivery"
ISIK_NOTIFICATIONS_DELIVERY_ATTEMPT_MODEL = "owned.DeliveryAttempt"
ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL = "owned.Subscription"
ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL = "owned.InboxItem"
ISIK_NOTIFICATIONS_ENDPOINT_MODEL = "owned.Endpoint"
ISIK_NOTIFICATIONS_ENDPOINT_KIND_MODEL = "owned.EndpointKind"

ISIK_FEATURES_HOLDER_MODEL = "owned.Organization"
ISIK_FEATURES_FEATURE_MODEL = "owned.Feature"
ISIK_FEATURES_GRANT_MODEL = "owned.FeatureGrant"
ISIK_FEATURES_SWEEP_MODEL = "owned.SweepCursor"
ISIK_FEATURES_OVERRIDES_MODEL = "owned.OrganizationOverrides"
ISIK_FEATURES = {
    "SETTINGS": "tests_swapped.owned.feature_settings",
    "GRANT_SOURCES": ("manual", "subscription"),
    "PEOPLE": "tests_swapped.owned.people.of",
}
