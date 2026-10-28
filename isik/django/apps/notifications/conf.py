"""
`ISIK_NOTIFICATIONS`, the one setting the notifications apps read - a dict whose keys each have a default:

    ISIK_NOTIFICATIONS = {
        "PROJECT_NAME": "Acme",
        "PARTITIONS": "acme.tenancy.schema_names",
        "IN_PARTITION": "django_tenants.utils.schema_context",
        "PARTITION_EXISTS": "django_tenants.utils.schema_exists",
    }

A hook - `REDACTOR`, `EMAIL_SENDER`, `EMAIL_ADDRESS`, `RECIPIENT_LANGUAGE`, `EMAIL_CONTEXT`,
`PARTITIONS`, `IN_PARTITION`, `PARTITION_EXISTS` - is a callable or its dotted path. `ISIK_NOTIFICATIONS`
keys this module doesn't know are refused by a system check.
"""

from datetime import timedelta

from django.conf import settings
from django.utils.module_loading import import_string


DEFAULTS = {
    # Names the webhook signature header, X-<PROJECT_NAME>-Signature, unless SIGNATURE["header"] does.
    "PROJECT_NAME": None,
    # Whether notifies() must be given a KindName from declare_kind() - never a bare name.
    "REQUIRE_DECLARED_KINDS": False,
    # Which attempts a DeliveryAttempt row is written for: "all", "failures" or "none".
    "RECORD_ATTEMPTS": "failures",
    # How long an attempt, and a notice every delivery of which is done, is kept. None keeps it.
    "ATTEMPT_RETENTION": timedelta(days=30),
    "NOTICE_RETENTION": timedelta(days=90),
    # How much of a response body an attempt keeps.
    "MAX_RESPONSE_BYTES": 4096,
    # Attempts a delivery gets before it's abandoned - in each run, a manual retry starting a new one - and
    # the seconds to wait after each failed one, the last repeating once they run out.
    "MAX_ATTEMPTS": 5,
    "BACKOFF": (60, 300, 1800, 7200, 21600),
    # The HTTP statuses a webhook failure is tried again for. Any other one is permanent.
    "RETRYABLE_STATUSES": (408, 425, 429, *range(500, 600)),
    # The channels a kind may name, each a registered channel's name.
    "CHANNELS": ("inbox", "email", "webhook"),
    # {"header": the webhook signature header's name, over X-<PROJECT_NAME>-Signature}.
    "SIGNATURE": {},
    # Seconds a webhook POST may take in all, and the consecutive failures that park its endpoint.
    "ENDPOINT_TIMEOUT": 10,
    "DISABLE_AFTER": 10,
    # Whether a webhook may reach a non-public address - loopback, private, link-local and the like: False,
    # True, or the networks it may reach ("10.1.0.0/16").
    "ALLOW_PRIVATE_TARGETS": False,
    # The proxy webhooks go out through, or None to connect directly. The environment's is never used.
    "WEBHOOK_PROXY": None,
    # f(dict) -> dict, applied to an attempt's request and response before they're written.
    "REDACTOR": None,
    # f(delivery, notice, address) sending one email - by default from templates named by the kind.
    "EMAIL_SENDER": "isik.django.apps.notifications.channels.email.send_from_templates",
    # f(user) -> the address to email, or None. By default the user model's email field.
    "EMAIL_ADDRESS": None,
    # f(user) -> the language a user's email is written in, or None for the site's. By default their
    # `language`, when they have one that isn't empty.
    "RECIPIENT_LANGUAGE": "isik.django.apps.notifications.channels.email.language_of",
    # f(delivery, notice) -> a dict every email the shipped senders render starts from - the site's name,
    # say - under the kind's own context, which wins a key both name.
    "EMAIL_CONTEXT": None,
    # f() -> every partition's key, and f(key) -> a context manager entering one. Both None: one partition.
    "PARTITIONS": None,
    "IN_PARTITION": None,
    # f(key) -> whether the partition still exists. By default, whether PARTITIONS() lists it.
    "PARTITION_EXISTS": None,
    # Where a trigger-sourced kind declaring no partition= reads its row's partition from - a resolver from
    # isik.django.apps.notifications.partitions. None stores no partition.
    "DEFAULT_PARTITION": None,
    # Notices one fan-out takes at a time; how long a claimed delivery or shard may stay unfinished before
    # reap_stranded takes it back; and the most rows one reap or prune touches.
    # Whether a fan-out is started after every Celery task that may have written a notice - for a project
    # whose beat doesn't run fan_out. See hooks.py.
    # The history context's key naming who acted, copied onto each notice as actor_id; None records no one.
    "ACTOR_CONTEXT_KEY": "user",
    # The history context's keys each notice keeps in its context, beside the actor - what's needed to read
    # actor_id back, such as the schema the actor lives in.
    "NOTICE_CONTEXT_KEYS": (),
    # The endpoint's lookup naming its owner - "owner", or "organization__members" - when endpoints are
    # owned: each then receives only notices whose audience holds its owner. None: every endpoint is the
    # deployment's, and receives every notice of the kinds it wants.
    "ENDPOINT_OWNER_FIELD": None,
    # Why endpoints keyed to a user are still the deployment's, with no ENDPOINT_OWNER_FIELD: an
    # exemptions.SharedEndpoints(reason=...), which quiets isik.W008.
    "ENDPOINTS_SHARED": None,
    "FAN_OUT_AFTER_TASKS": False,
    # What starts a fan-out where isik can't see it - a cron, django-celery-beat's rows: a
    # exemptions.ScheduledElsewhere(reason=...), which quiets isik.W006.
    "FAN_OUT_BY": None,
    # Why sending webhooks inside the writing request or task is fine: an exemptions.SendsInline(reason=...), which
    # quiets isik.W007.
    "SENDS_INLINE": None,
    "BATCH_SIZE": 100,
    "STRANDED_AFTER": timedelta(minutes=10),
    "HOUSEKEEPING_BATCH": 1000,
}

HOOKS = (
    "REDACTOR",
    "EMAIL_SENDER",
    "EMAIL_ADDRESS",
    "RECIPIENT_LANGUAGE",
    "EMAIL_CONTEXT",
    "PARTITIONS",
    "IN_PARTITION",
    "PARTITION_EXISTS",
)


def configured():
    """`ISIK_NOTIFICATIONS` as the project set it."""
    return getattr(settings, "ISIK_NOTIFICATIONS", None) or {}


def setting(name):
    """`name`'s value: the project's, else its default. A hook given by its dotted path is imported."""
    value = configured().get(name, DEFAULTS[name])
    if name in HOOKS and isinstance(value, str):
        return import_string(value)
    return value


def partitioned():
    """Whether the project declares partitions - `PARTITIONS` and `IN_PARTITION` both set."""
    return setting("PARTITIONS") is not None and setting("IN_PARTITION") is not None


def signature_header():
    """The webhook signature header's name: `SIGNATURE["header"]`, else X-<PROJECT_NAME>-Signature, else None."""
    header = setting("SIGNATURE").get("header")
    if header:
        return header
    name = setting("PROJECT_NAME")
    if not name:
        return None
    return f"X-{'-'.join(name.split())}-Signature"
