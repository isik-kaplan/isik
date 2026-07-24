"""
Real Django settings used to run the test suite, not a project template. Connection
details come from the environment so the same settings work locally and in CI.
"""

import getpass
import os


SECRET_KEY = "not-a-secret-just-for-tests"

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "isik_test"),
        "USER": os.environ.get("POSTGRES_USER", getpass.getuser()),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", ""),
        "PORT": os.environ.get("POSTGRES_PORT", ""),
    }
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "dalf",
    "django_object_actions",
    "pgtrigger",
    "pghistory",
    "isik.django.apps.common",
    "isik.django.apps.feedback",
    "isik.django.apps.tags",
    "tests.testapp",
]

# testapp ships real migrations (tests/testapp/migrations) rather than --run-syncdb:
# syncdb'd apps are created before migrated apps' migrations run, which breaks the
# FK from Note to ContentType.

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(os.path.dirname(__file__), "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

AUTH_PASSWORD_VALIDATORS = []

USE_TZ = True

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(os.path.dirname(__file__), "media")

SESSION_HEADER_NAME = "X-Session-Key"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# USERNAME_FIELD = "email" here - see tests/testapp/models.py.
AUTH_USER_MODEL = "testapp.EmailUser"

REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
}

FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "tiptap_schema.json")
