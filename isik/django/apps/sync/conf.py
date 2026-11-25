"""
`ISIK['DJANGO']['SYNC']`, the setting the sync app reads - a dict whose keys each have a default:

    ISIK['DJANGO']['SYNC'] = {
        "RETENTION": timedelta(days=60),
        "OFFLINE_PERIOD": timedelta(days=30),
        "PUSH": {"OPTIONS": {"MAX_ENTRIES": 500, "MAX_BYTES": 2_000_000, "THROTTLE_SCOPE": "sync_push"}},
    }

Every limit is the project's to set: unset, isik imposes none. Keys this module doesn't know are refused by a
system check, `isik.E027`.
"""

from django.utils.module_loading import import_string

from isik.django.conf import django_area
from isik.django.public_names import SYNC_PROTOCOL_HEADER


DEFAULTS = {
    # The sync log model, "app_label.Model" - isik's, unless the project owns it (see bases.py).
    "LOG_MODEL": "isik_sync.SyncLogEntry",
    # How long a change stays in the log, a timedelta: a cursor older than what pruning removed is answered
    # 410, and its client pulls a snapshot. None keeps every change.
    "RETENTION": None,
    # The longest a client is meant to sync after being offline, a timedelta - what RETENTION and the
    # idempotency claims' RETENTION are held to. None holds them to nothing.
    "OFFLINE_PERIOD": None,
    # The most row changes one page of the feed carries, whatever the client asks. None serves what it asks.
    "PAGE_LIMIT": None,
    # How long the oldest open transaction may hold the feed back before each page served logs a warning naming
    # its process, a timedelta. None logs none.
    "LAG_WARNING": None,
    # {"BACKEND": a PushBackend subclass or its dotted path, "OPTIONS": {...}} - what runs a push's entries.
    # The options every backend takes: MAX_ENTRIES and MAX_BYTES, the most entries and bytes one push holds,
    # and THROTTLE_SCOPE, the DRF throttle scope counting a person's entries - each None, none, unless set.
    "PUSH": {},
    # The header every sync request and response names its protocol version in - a client sends the same.
    "PROTOCOL_HEADER": SYNC_PROTOCOL_HEADER.default,
}

# The backend a push runs on when PUSH names none.
DEFAULT_PUSH_BACKEND = "isik.django.apps.sync.push.ViewPushBackend"
PUSH_KEYS = ("BACKEND", "OPTIONS")
PUSH_OPTIONS = ("MAX_ENTRIES", "MAX_BYTES", "THROTTLE_SCOPE")


def configured():
    """`ISIK['DJANGO']['SYNC']` as the project set it."""
    area = django_area("SYNC")
    return area if isinstance(area, dict) else {}


def setting(name):
    """`name`'s value: the project's, else its default."""
    return configured().get(name, DEFAULTS[name])


def push_backend_class():
    """The push backend's class: `PUSH["BACKEND"]`, imported when it's a dotted path - isik's own unless set."""
    backend = setting("PUSH").get("BACKEND", DEFAULT_PUSH_BACKEND)
    return import_string(backend) if isinstance(backend, str) else backend


def push_backend():
    """A push backend, built with `PUSH["OPTIONS"]`."""
    return push_backend_class()(dict(setting("PUSH").get("OPTIONS") or {}))
