"""
The sync endpoints - `SyncViewSet`, which a project routes - and what a synced model's own REST API needs:
`ClientPrimaryKeyMixin`, creating a row under the id its client made, and `PerEntryThrottle`, marking a throttle
push runs for every entry.

    router = SimpleRouter(trailing_slash=False)
    router.register("sync", SyncViewSet, basename="sync")   # /sync/capabilities, /sync/changes, /sync/push
"""

import hashlib
import json

from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.utils.encoders import JSONEncoder
from rest_framework.validators import UniqueValidator

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.django.apps.common.atomic import not_atomic
from isik.django.apps.sync import conf
from isik.django.apps.sync.feed import CursorExpired, Feed
from isik.django.drf.viewsets.base import BaseViewSet
from isik.django.drf.viewsets.preconditions import matches


PROTOCOL_HEADER = "Isik-Sync-Protocol"
# The protocol versions this server speaks.
PROTOCOLS = (1,)


class ProtocolUnsupported(Exception):
    """A request naming no protocol version this server speaks - answered 400, naming those it does."""


class PrimaryKeyTaken(APIException):
    """A create naming an id a row has already - 409."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = gettext_lazy("A row with this id exists already.")
    default_code = "primary_key_taken"


class ClientPrimaryKeyMixin:
    """
    A model serializer creating its row under the id the client sends - so a client offline makes the row's id
    itself, and refers to the row by it in the writes it queues after. The id is optional on a create: one not sent
    is made as ever. An id a row has already is refused with 409, `primary_key_taken`. After the create, the id
    is read-only, as before.
    """

    def get_fields(self):
        fields = super().get_fields()
        pk = self.Meta.model._meta.pk
        if self.instance is None and pk.name in fields:
            fields[pk.name] = self.build_client_primary_key(pk)
        return fields

    def build_client_primary_key(self, pk):
        """The field taking the id on a create - the model field's own, writable and optional."""
        field_class, kwargs = self.build_standard_field(pk.name, pk)
        # An id taken is a 409 the create answers, not a 400 a validator would.
        validators = [each for each in kwargs.get("validators", ()) if not isinstance(each, UniqueValidator)]
        kwargs.update(read_only=False, required=False, validators=validators)
        return field_class(**kwargs)

    def create(self, validated_data):
        model = self.Meta.model
        pk = validated_data.get(model._meta.pk.name)
        if pk is not None and model._default_manager.filter(pk=pk).exists():
            raise PrimaryKeyTaken()
        try:
            # A savepoint of its own: a concurrent create taking the id first fails the insert, not the request.
            with transaction.atomic():
                return super().create(validated_data)
        except IntegrityError:
            if pk is not None and model._default_manager.filter(pk=pk).exists():
                raise PrimaryKeyTaken() from None
            raise


class PerEntryThrottle:
    """
    Marks a throttle that guards a cost each write runs up - a text sent, a paid API called - so push runs it for
    every entry, against the person's real rate: an entry over it is answered 429, and the push goes on. Push runs
    none of a view's other throttles, which would count each entry as a request; its own scope counts entries.

        class TextsThrottle(PerEntryThrottle, UserRateThrottle):
            scope = "texts"
    """


class SyncPushThrottle(SimpleRateThrottle):
    """Push's own throttle: the backend's `THROTTLE_SCOPE`, counting each entry pushed as a request."""

    def __init__(self, scope):
        self.scope = scope
        super().__init__()

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": request.user.pk}

    def allow_request(self, request, view):
        self.key = self.get_cache_key(request, view)
        self.history = self.cache.get(self.key, [])
        self.now = self.timer()
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()
        count = view.entry_count(request)
        if len(self.history) + count > self.num_requests:
            return self.throttle_failure()
        self.history[:0] = [self.now] * count
        self.cache.set(self.key, self.history, self.duration)
        return True


class PushEntrySerializer(serializers.Serializer):
    key = serializers.CharField()
    method = serializers.ChoiceField(choices=["POST", "PUT", "PATCH", "DELETE"])
    model = serializers.CharField()
    id = serializers.CharField()
    body = serializers.DictField(required=False)
    if_match = serializers.CharField(required=False)


class PushSerializer(serializers.Serializer):
    entries = PushEntrySerializer(many=True)


def page_etag(body):
    """The ETag of a page of the feed: its body's digest - a page served again the same is the same."""
    text = json.dumps(body, cls=JSONEncoder, sort_keys=True, separators=(",", ":"))
    return f'"{hashlib.sha256(text.encode()).hexdigest()[:32]}"'


@not_atomic(
    "push commits each entry in a transaction of its own, so a long push holds no transaction open - which would "
    "hold the feed back for everyone - and the feed reads the log in statements of its own"
)
class SyncViewSet(BaseViewSet):
    """
    The sync endpoints, isik's sync protocol version 1:

    - `GET capabilities` - `{protocol, push: {backend, max_entries, max_bytes}}`.
    - `GET changes?cursor=&limit=&snapshot=` - a page of the person's changes, `{changes, cursor, has_more}`, with
      an `ETag`: `If-None-Match` naming it answers 304. A cursor past retention is answered 410.
    - `POST push` - `{entries: [...]}`, answered `{results: [...]}` - 413 for one holding more than the backend's
      limits.

    Every request names its protocol version in `Isik-Sync-Protocol`, and a missing or unknown one is refused
    with 400. Every response names it too. A person must be authenticated - 401 otherwise.
    """

    permission_classes = [IsAuthenticated]

    def get_authenticate_header(self, request):
        # 401, never 403, for a person not authenticated: the protocol pauses a client on a 401.
        return super().get_authenticate_header(request) or 'Session realm="api"'

    def initial(self, request, *args, **kwargs):
        if self.action == "push":
            # Read before anything parses it: its size is held to MAX_BYTES.
            self.pushed_bytes = len(request._request.body)
        super().initial(request, *args, **kwargs)
        if request.headers.get(PROTOCOL_HEADER) not in [str(protocol) for protocol in PROTOCOLS]:
            raise ProtocolUnsupported()

    def handle_exception(self, exc):
        if isinstance(exc, ProtocolUnsupported):
            return Response(
                {"code": "sync_protocol_unsupported", "supported": list(PROTOCOLS)}, status=status.HTTP_400_BAD_REQUEST
            )
        if isinstance(exc, CursorExpired):
            return Response({"code": "sync_cursor_expired"}, status=status.HTTP_410_GONE)
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response[PROTOCOL_HEADER] = str(PROTOCOLS[-1])
        return response

    def get_throttles(self):
        if self.action != "push":
            return super().get_throttles()
        scope = conf.push_backend().throttle_scope
        return [] if scope is None else [SyncPushThrottle(scope)]

    def entry_count(self, request):
        """How many entries a push holds, for push's throttle - one for a body that holds none."""
        entries = request.data.get("entries") if isinstance(request.data, dict) else None
        return len(entries) if isinstance(entries, list) and entries else 1

    @action(detail=False, methods=["get"])
    def capabilities(self, request):
        backend = conf.push_backend()
        return Response(
            {
                "protocol": PROTOCOLS[-1],
                "push": {
                    "backend": backend.advertised_name,
                    "max_entries": backend.max_entries,
                    "max_bytes": backend.max_bytes,
                },
            }
        )

    @action(detail=False, methods=["get"])
    def changes(self, request):
        limit = request.query_params.get("limit")
        if limit is not None:
            if not limit.isdigit() or int(limit) < 1:
                raise ValidationError({"limit": [_("A whole number of at least 1.")]})
            limit = int(limit)
        most = conf.setting("PAGE_LIMIT")
        if most is not None:
            limit = most if limit is None else min(limit, most)
        cursor = request.query_params.get("cursor")
        body = Feed(request, cursor, limit, request.query_params.getlist("snapshot")).page()
        etag = page_etag(body)
        header = request.headers.get("If-None-Match")
        if header is not None and matches(header, etag):
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        return Response(body, headers={"ETag": etag})

    @action(detail=False, methods=["post"])
    def push(self, request):
        backend = conf.push_backend()
        pushed = PushSerializer(data=request.data)
        pushed.is_valid(raise_exception=True)
        entries = pushed.validated_data["entries"]
        too_many = backend.max_entries is not None and len(entries) > backend.max_entries
        too_big = backend.max_bytes is not None and self.pushed_bytes > backend.max_bytes
        if too_many or too_big:
            return Response(
                {"code": "sync_push_too_large", "max_entries": backend.max_entries, "max_bytes": backend.max_bytes},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        return Response({"results": backend.run(request, [dict(entry) for entry in entries])})
