"""IdempotencyMixin - see its own docstring."""

import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import router, transaction
from rest_framework.exceptions import NotAuthenticated, ValidationError

from isik._internal.reasons import require_reasons
from isik._internal.translation import gettext as _
from isik.django.apps.idempotency.claims import claim_idempotency_key, get_claim_model
from isik.django.apps.idempotency.exceptions import IdempotencyKeyNotReplayable, IdempotencyKeyReused
from isik.django.apps.idempotency.fingerprint import request_fingerprint


class IdempotentReplay(Exception):
    """Raised from `initial()` to answer a retry with `response` instead of running its handler."""

    def __init__(self, response):
        self.response = response


class IdempotencyMixin:
    """
    Honors an `Idempotency-Key: <uuid>` header on a DRF view: the same key with the same request
    answers with the first request's response, status and all, rather than doing the work twice.

        class WidgetViewSet(IdempotencyMixin, BaseModelViewSet):
            model = Widget
            endpoint = "widgets"
            serializer_class = WidgetSerializer

        POST /widgets/  Idempotency-Key: 9b1d...   -> 201 {"id": 7, ...}       (creates widget 7)
        POST /widgets/  Idempotency-Key: 9b1d...   -> 201 {"id": 7, ...}       Idempotent-Replayed: true
        POST /widgets/  Idempotency-Key: 9b1d...   -> 422                      (same key, different body)

    The claim is inserted inside the request's transaction, after authentication, permissions and
    throttling, and before the handler runs - so it needs `ATOMIC_REQUESTS` (or the view wrapped in
    `transaction.atomic` some other way), and raises `ImproperlyConfigured` without one. A concurrent
    retry waits for the first request to end, then replays it if it committed, or runs for real if it
    didn't. A replay re-runs the view's permissions, so a caller who lost access is refused.

    A failure doesn't spend the key: a raised error rolls the claim back with everything else, and a
    response of 400 or above that a handler returns instead releases it. Either way, a retry is a real
    attempt rather than a replayed refusal.

    - `idempotent_methods` - which methods are covered. `("POST",)`.
    - `idempotency_key_required` - a covered request without the header is a 400. True; False honors
      a key only when one is sent.
    - `idempotency_exempt_actions` - `{action: reason}`, actions the key is never asked of.
    - `idempotency_no_replay_actions` - `{action: reason}`, actions whose response mustn't be stored
      (a secret shown once): a repeat is refused with 409, and the claim keeps only its status.
    - `idempotency_normalize` - what a request's body is compared by, a function of the request (see
      `request_fingerprint()`). Per action too: `@action(..., idempotency_normalize=fn)`.
    - `idempotency_claim_model` - the claim model, else `get_claim_model()` picks it.

    A covered request needs an authenticated user, since a claim is scoped to `request.user` - an
    anonymous one is refused with `NotAuthenticated`. Override `get_idempotency_owner()` to change that.
    """

    idempotency_header = "Idempotency-Key"
    idempotency_replayed_header = "Idempotent-Replayed"
    idempotent_methods = ("POST",)
    idempotency_key_required = True
    idempotency_exempt_actions = {}
    idempotency_no_replay_actions = {}
    idempotency_normalize = None
    idempotency_claim_model = None

    # Per request: the claim this request made, and whether it was answered by a replay instead.
    idempotency_claim = None
    idempotent_replayed = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for attribute in ("idempotency_exempt_actions", "idempotency_no_replay_actions"):
            require_reasons(cls.__name__, attribute, getattr(cls, attribute))

    @property
    def idempotency_action(self):
        """The viewset action being served - None on a view that isn't a viewset, which has no actions."""
        # No mutant of the default is told apart: no action dict holds None or what a mutant puts there.
        return getattr(self, "action", None)  # pragma: no mutate

    def is_idempotent(self, request):
        """Whether this request is covered - its method is, and its action isn't exempt."""
        exempt = self.idempotency_action in self.idempotency_exempt_actions
        return request.method in self.idempotent_methods and not exempt

    def get_idempotency_key(self, request):
        """The key the request sent, as a UUID - None if it sent none and none is required."""
        sent = request.headers.get(self.idempotency_header)
        if sent is None:
            if self.idempotency_key_required:
                raise ValidationError({self.idempotency_header: [_("This header is required.")]}, code="required")
            return None
        try:
            return uuid.UUID(sent)
        except ValueError:
            raise ValidationError({self.idempotency_header: [_("Must be a UUID.")]}) from None

    def get_idempotency_owner(self, request):
        """Who the claim is scoped to - two callers sending the same key never meet."""
        if not request.user.is_authenticated:
            raise NotAuthenticated()
        return request.user

    def get_idempotency_normalize(self):
        # An @action's own idempotency_normalize= lands on the instance; the class's is read off the
        # class, so a plain function stays a function of the request rather than becoming a method.
        return self.__dict__.get("idempotency_normalize") or type(self).idempotency_normalize

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not self.is_idempotent(request):
            return
        key = self.get_idempotency_key(request)
        if key is None:
            return
        owner = self.get_idempotency_owner(request)
        model = get_claim_model(self.idempotency_claim_model)
        if not transaction.get_connection(router.db_for_write(model)).in_atomic_block:
            raise ImproperlyConfigured(
                _(
                    "%(view)s honors idempotency keys, which needs its request served in a transaction - "
                    "turn on ATOMIC_REQUESTS."
                )
                % {"view": type(self).__name__}
            )
        fingerprint = request_fingerprint(request, self.get_idempotency_normalize())
        claim, created = claim_idempotency_key(
            model, owner, key, fingerprint, lock_timeout=getattr(settings, "IDEMPOTENCY_LOCK_TIMEOUT", None)
        )
        if created:
            self.idempotency_claim = claim
            return
        if claim.fingerprint != fingerprint:
            raise IdempotencyKeyReused()
        if self.idempotency_action in self.idempotency_no_replay_actions:
            raise IdempotencyKeyNotReplayable()
        raise IdempotentReplay(claim.replay(self, request))

    def handle_exception(self, exc):
        if isinstance(exc, IdempotentReplay):
            self.idempotent_replayed = True
            return exc.response
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        claim = self.idempotency_claim
        if self.idempotent_replayed:
            response[self.idempotency_replayed_header] = "true"
        elif claim is not None and response.status_code >= 400:
            # A raised error already marked the transaction for rollback, which takes the claim with it -
            # and refuses any further query. A returned one didn't, so the claim is released here.
            if not transaction.get_rollback(router.db_for_write(type(claim))):
                claim.delete()
        elif claim is not None:
            claim.record(self, response, replayable=self.idempotency_action not in self.idempotency_no_replay_actions)
        return response
