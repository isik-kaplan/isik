"""
`ISIK['DJANGO']['INBOUND']`, the setting the inbound app reads - a dict whose keys each have a default:

    ISIK['DJANGO']['INBOUND'] = {
        "SOURCES": "billing.inbound_sources",
        "BACKOFF": (60, 300, 1800),
        "STRANDED_AFTER": timedelta(minutes=10),
    }

Every limit is the project's to set: unset, isik imposes none - but BACKOFF and STRANDED_AFTER, which an event
can't be tried again or taken back without, are required while the app is installed. A hook - `PARTITIONS`,
`IN_PARTITION` - is a callable or its dotted path. Keys this module doesn't know are refused by a system check,
`isik.E028`.
"""

from django.utils.module_loading import import_string

from isik.django.conf import django_area


# EVENT_MODEL - the inbound event model, "app_label.Model" - is read by swapping.py: isik's, unless the project
# owns it (see bases.py).
DEFAULTS = {
    # The module declaring the sources and their handlers, imported when the app is ready.
    "SOURCES": None,
    # The seconds to wait after each failed attempt to handle an event, the last repeating once they run out.
    "BACKOFF": None,
    # The attempts an event gets before it's given up on - abandoned, kept, and tried again by hand. None tries
    # it for as long as it fails.
    "MAX_ATTEMPTS": None,
    # How long an event may stay claimed by a worker before work() takes it back - a worker that died with it.
    "STRANDED_AFTER": None,
    # How long an event is kept once it's done - handled, ignored, or given up on - a timedelta. None keeps it.
    "RETENTION": None,
    # The most events one work() takes back or prunes at a time. None takes every one.
    "BATCH": None,
    # f() -> every partition's key, and f(key) -> a context manager entering one: where events are written,
    # for a project whose sources say which partition a request is for. Both None: one partition.
    "PARTITIONS": None,
    "IN_PARTITION": None,
}

HOOKS = ("PARTITIONS", "IN_PARTITION")


def configured():
    """`ISIK['DJANGO']['INBOUND']` as the project set it."""
    area = django_area("INBOUND")
    return area if isinstance(area, dict) else {}


def setting(name):
    """`name`'s value: the project's, else its default. A hook given by its dotted path is imported."""
    value = configured().get(name, DEFAULTS[name])
    if name in HOOKS and isinstance(value, str):
        return import_string(value)
    return value


def partitioned():
    """Whether the project declares partitions - `PARTITIONS` and `IN_PARTITION` both set."""
    return setting("PARTITIONS") is not None and setting("IN_PARTITION") is not None
