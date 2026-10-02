"""
The claim models (abstract), get_claim_model(), claim_idempotency_key() and the body codecs - see
each one's own docstring.
"""

import json
from contextlib import contextmanager

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, OperationalError, connections, models, router, transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.django.apps.idempotency.exceptions import IdempotencyKeyInFlight, IdempotentReplayGone


# The app each shipped claim lives in, and the model it is.
CLAIM_APPS = {
    "isik.django.apps.idempotency.by_reference": "idempotency_by_reference.IdempotencyClaim",
    "isik.django.apps.idempotency.with_body": "idempotency_with_body.IdempotencyClaimWithBody",
}

# Postgres' SQLSTATE for lock_not_available - what running out of lock_timeout raises.
LOCK_NOT_AVAILABLE = "55P03"


class AbstractBaseIdempotencyClaim(models.Model):
    """
    One spent idempotency key: who spent it, on what request (`fingerprint`), and the status it was
    answered with. Unique per `(claimed_by, key)` - that index is what makes a concurrent retry wait
    for the first request's transaction, then replay it.

    A claim is inserted inside the request's transaction before the handler runs, so it exists only
    if its request committed: no in-flight state, nothing to reap after a crash, no expiry. `status`
    is null only while its request is being served, which no other transaction can see.

    Subclasses decide what a replay sends back - `keep_response()` records it, `replay()` sends it.
    `AbstractIdempotencyClaim` and `AbstractIdempotencyClaimWithBody` are the two shipped ways.
    """

    id = models.BigAutoField(
        primary_key=True,
        help_text=gettext_lazy("The claim's own identifier."),
        db_comment="The claim's own identifier.",
    )
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        help_text=gettext_lazy("Who spent the key - a key is only ever compared with its caller's own."),
        db_comment="Who spent the key - a key is only ever compared with its caller's own.",
    )
    key = models.UUIDField(
        help_text=gettext_lazy("The Idempotency-Key the caller sent."),
        db_comment="The Idempotency-Key the caller sent.",
    )
    fingerprint = models.CharField(
        max_length=64,
        help_text=gettext_lazy("sha256 of the request the key was spent on - its method, path, query and body."),
        db_comment="sha256 of the request the key was spent on - its method, path, query and body.",
    )
    status = models.PositiveSmallIntegerField(
        null=True,
        help_text=gettext_lazy("The status the request was answered with."),
        db_comment="The status the request was answered with. Null only while its request is being served.",
    )
    claimed_at = models.DateTimeField(
        default=timezone.now,
        help_text=gettext_lazy("When the key was spent."),
        db_comment="When the key was spent. Nothing expires by it - it is there to delete old claims by.",
    )

    class Meta:
        abstract = True
        constraints = [models.UniqueConstraint(fields=["claimed_by", "key"], name="%(app_label)s_%(class)s_key")]

    def record(self, view, response, replayable=True):
        """Records `response` as what this claim's request was answered with. An action that can't be
        replayed keeps only its status - its response is never stored, whatever this claim can hold."""
        self.status = response.status_code
        if replayable:
            self.keep_response(view, response)
        self.save()

    def keep_response(self, view, response):
        """Stores whatever `replay()` needs to answer a retry - on `self`, which `record()` then saves."""
        raise NotImplementedError

    def replay(self, view, request):
        """The response a retry of this claim's request is answered with."""
        raise NotImplementedError


def replayed_object_id(view, response):
    """
    The pk of the row `response` serializes, as `AbstractIdempotencyClaim` stores it - or None for a
    response with no body. Raises `ImproperlyConfigured` unless the body is the view's own serializer
    over one of its own rows, since that's what a replay re-serializes it with.
    """
    if response.data is None:
        return None
    serializer = getattr(response.data, "serializer", None)
    instance = getattr(serializer, "instance", None)
    if type(serializer) is not view.get_serializer_class() or not isinstance(instance, view.get_queryset().model):
        raise ImproperlyConfigured(
            _(
                "%(view)s.%(action)s can't be replayed by reference: its response isn't its own serializer "
                "over one of its own rows. Declare it in idempotency_no_replay_actions, or keep response bodies "
                "with an AbstractIdempotencyClaimWithBody."
            )
            % {"view": type(view).__name__, "action": view.idempotency_action}
        )
    return str(instance.pk)


class AbstractIdempotencyClaim(AbstractBaseIdempotencyClaim):
    """
    A claim replayed by reference: it keeps the pk of the row its response serialized, never the
    body. A replay fetches that row through the view's own `get_queryset()`, checks the view's object
    permissions on it, and serializes it with the view's own serializer - so nothing is at rest that
    isn't already in the table it came from, and a caller who lost access is refused rather than
    answered from a copy. A row that's gone since answers 410.

    An action is replayable this way iff its response is the view's serializer over one of the
    view's own rows (or has no body). Any other response is refused at `record()` with
    `ImproperlyConfigured` - inside the request's transaction, so the work it did rolls back.
    """

    object_id = models.CharField(
        max_length=255,
        null=True,
        help_text=gettext_lazy("The primary key of the row the response serialized, to serialize it again."),
        db_comment="The primary key of the row the response serialized - null for a response without a body.",
    )

    class Meta(AbstractBaseIdempotencyClaim.Meta):
        abstract = True

    def keep_response(self, view, response):
        self.object_id = replayed_object_id(view, response)

    def replay(self, view, request):
        if self.object_id is None:
            return Response(status=self.status)
        instance = view.get_queryset().filter(pk=self.object_id).first()
        if instance is None:
            raise IdempotentReplayGone()
        view.check_object_permissions(request, instance)
        return Response(view.get_serializer(instance).data, status=self.status)


class JSONBodyCodec:
    """The default body codec: the response's data as JSON, written with DRF's own encoder."""

    def encode(self, data):
        return json.dumps(data, cls=JSONEncoder)

    def decode(self, text):
        return json.loads(text)


def configured_body_codec():
    """
    `IDEMPOTENCY_BODY_CODEC` - a codec, a codec class, or a dotted path to either - else
    `JSONBodyCodec`. A class is instantiated with no arguments.
    """
    codec = getattr(settings, "IDEMPOTENCY_BODY_CODEC", None) or JSONBodyCodec
    if isinstance(codec, str):
        codec = import_string(codec)
    return codec() if isinstance(codec, type) else codec


class AbstractIdempotencyClaimWithBody(AbstractBaseIdempotencyClaim):
    """
    A claim replayed from the response body it kept - for responses that can't be re-derived from a
    row. The body goes through a codec on its way in and out: `body_codec` on the model, else
    `configured_body_codec()`. A codec is anything with `encode(data) -> str` and `decode(str) -> data`,
    so encrypting the body at rest is a codec that encrypts what `JSONBodyCodec` writes.
    """

    body = models.TextField(
        null=True,
        help_text=gettext_lazy("The response body, as its codec wrote it."),
        db_comment="The response body, as its codec wrote it - null for an action that can't be replayed.",
    )

    body_codec = None

    class Meta(AbstractBaseIdempotencyClaim.Meta):
        abstract = True

    def get_body_codec(self):
        return self.body_codec or configured_body_codec()

    def keep_response(self, view, response):
        self.body = self.get_body_codec().encode(response.data)

    def replay(self, view, request):
        return Response(self.get_body_codec().decode(self.body), status=self.status)


def get_claim_model(model=None):
    """
    The claim model to use: `model` (a model or an `"app_label.Model"` string), else
    `IDEMPOTENCY_CLAIM_MODEL`, else whichever of the two shipped claim apps is installed. Raises
    `ImproperlyConfigured` when that leaves none, or both.
    """
    model = model or getattr(settings, "IDEMPOTENCY_CLAIM_MODEL", None)
    if model is None:
        installed = [claim for app, claim in CLAIM_APPS.items() if apps.is_installed(app)]
        if len(installed) != 1:
            raise ImproperlyConfigured(
                _("Install exactly one of %(apps)s, or set IDEMPOTENCY_CLAIM_MODEL to the claim model to use.")
                % {"apps": " and ".join(CLAIM_APPS)}
            )
        model = installed[0]
    return apps.get_model(model) if isinstance(model, str) else model


@contextmanager
def _lock_timeout(using, milliseconds):
    if milliseconds is None:
        yield
        return
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT current_setting('lock_timeout')")
        previous = cursor.fetchone()[0]
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", [str(milliseconds)])
    yield
    # Only reached when the insert went through. When it didn't, rolling back its savepoint puts the
    # old value back on its own.
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", [previous])


def claim_idempotency_key(model, claimed_by, key, fingerprint, lock_timeout=None):
    """
    Claims `key` for `claimed_by`, returning `(claim, created)`. Must run inside the transaction of
    the work the key guards.

    The insert runs in a savepoint against the `(claimed_by, key)` index. When another transaction
    holds the same key, Postgres makes the insert wait for it to end: if it committed, the insert
    fails and its claim is returned with `created=False`; if it rolled back, the insert goes through
    and this request does the work for real.

    `lock_timeout` (milliseconds) bounds that wait, and running out raises `IdempotencyKeyInFlight`.
    None waits as long as the first request takes.
    """
    using = router.db_for_write(model)
    manager = model._default_manager.db_manager(using)
    try:
        with transaction.atomic(using=using), _lock_timeout(using, lock_timeout):
            return manager.create(claimed_by=claimed_by, key=key, fingerprint=fingerprint), True
    except IntegrityError:
        return manager.get(claimed_by=claimed_by, key=key), False
    except OperationalError as error:
        if getattr(error.__cause__, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            raise IdempotencyKeyInFlight() from error
        raise
