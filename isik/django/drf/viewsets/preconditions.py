"""PreconditionMixin - see its own docstring."""

import copy
from collections.abc import Mapping

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, NotAuthenticated, PermissionDenied
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import set_rollback

from isik._internal.reasons import require_exemptions
from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.common.utils.exemptions import Exemption
from isik.django.apps.common.db.concurrency import (
    ETAG_ANNOTATION,
    concurrency_etag,
    concurrency_field_name,
    concurrency_token_problems,
    current_concurrency_etag,
    has_concurrency_token,
)


class NoPrecondition(
    Exemption,
    rule="isik.precondition",
    changes_behavior=True,
    why=(
        "A write that doesn't say which version of the row it was made against (If-Match) overwrites "
        "whatever changed since that version was read."
    ),
):
    """Why an action is written without `If-Match` - `precondition_exempt_actions`' type."""


class NoReadableVersion(
    Exemption,
    rule="isik.precondition.readable-version",
    changes_behavior=True,
    why=(
        "A write that asks If-Match needs a read that hands out the version to send - a GET sending its "
        "ETag - or a client has nothing to name but a version it guessed."
    ),
):
    """Why a write that asks `If-Match` has no read handing out its version - `precondition_unread_actions`' type."""


class PreconditionRequired(APIException):
    """A write that must say which version it was made against sent no `If-Match` - or sent `*`, refused."""

    status_code = status.HTTP_428_PRECONDITION_REQUIRED
    default_detail = gettext_lazy("This request must say which version it was made against, in an If-Match header.")
    default_code = "precondition_required"


class PreconditionFailed(APIException):
    """
    The row changed since the version `If-Match` named - `instance` is the row as it is now, or None
    when it's gone and `missing_row_status` is 412.
    """

    status_code = status.HTTP_412_PRECONDITION_FAILED
    default_detail = gettext_lazy("This changed since the version the request was made against.")
    default_code = "precondition_failed"

    def __init__(self, instance):
        super().__init__()
        self.instance = instance


class NotModified(Exception):
    """Raised from `get_object()` to answer a read whose `If-None-Match` names the current version with 304."""

    def __init__(self, etag):
        self.etag = etag


# {option: its default}. Each is the setting PRECONDITION_<OPTION>, and a view's precondition_<option>
# overrides it - see PreconditionMixin.
PRECONDITION_DEFAULTS = {
    "ignores_weak_tags": False,
    "required": True,
    "allows_any": True,
    "missing_row_status": status.HTTP_404_NOT_FOUND,
    "not_modified": True,
}
MISSING_ROW_STATUSES = (status.HTTP_404_NOT_FOUND, status.HTTP_412_PRECONDITION_FAILED)


def entity_tags(header, *, ignores_weak=True):
    """
    The entity tags an `If-Match`/`If-None-Match` header lists - each without a `W/` prefix when
    `ignores_weak`, see `PreconditionMixin` for why. A comma never occurs inside one of isik's tags, so
    splitting on it is enough to find them.
    """
    tags = {tag.strip() for tag in header.split(",")}
    return {tag.removeprefix("W/") for tag in tags} if ignores_weak else tags


def matches(header, etag):
    """
    Whether an `If-None-Match` header names `etag`, or is `*` - any current version. Compared weakly,
    as RFC 9110 has `If-None-Match` compared.
    """
    tags = entity_tags(header)
    return "*" in tags or etag in tags


def writes_one_object(method, detail):
    """Whether a request writes one object: PUT, PATCH or DELETE, or POST to one (`detail=True`) - a create has none."""
    return method in ("PUT", "PATCH", "DELETE") or (method == "POST" and bool(detail))


def requires_precondition(view, method, action, detail):
    """
    Whether `view` (a `PreconditionMixin` class or instance) asks `If-Match` of `method` on `action`:
    the request writes one object, the method is one of its `precondition_methods`, and the action
    isn't exempt.
    """
    if method not in view.precondition_methods or action in view.precondition_exempt_actions:
        return False
    return writes_one_object(method, detail)


class PreconditionMixin:
    """
    Optimistic concurrency (RFC 9110's conditional requests) on a DRF view over one model: a write
    names the version of the row it was made against, and is refused if the row has moved on - so a
    page loaded before someone else saved can't silently overwrite what they saved.

        class WidgetViewSet(BaseModelViewSet):  # carries it; or PreconditionMixin on any GenericAPIView
            ...

        GET    /widgets/7/                                -> 200  ETag: "1760000000123456"
        GET    /widgets/7/  If-None-Match: "1760...456"   -> 304
        PATCH  /widgets/7/  If-Match: "1760...456"        -> 200  ETag: "1760000000654321"
        PATCH  /widgets/7/  If-Match: "1760...456"        -> 412  {"detail", "code", "current"}
        PATCH  /widgets/7/                                -> 428  {"detail", "code"}

    The token is the model's concurrency token (`isik.django.apps.common.db.concurrency`) - `updated_at`
    on a `BaseModel`, `version` on a `VersionedModel` - as an opaque, strong `ETag`. A client sends it
    back as it got it. `ConcurrencyTokenField` puts the same string in the body, for a list's items.

    PUT, PATCH, DELETE and POST to one object (a `detail=True` action) need `If-Match` (428 without
    one), unless the action is in `precondition_exempt_actions` - `{action: reason}`, each reason a
    `NoPrecondition`. The check runs in `get_object()`, after authentication, permissions and the
    404, and there the row is locked (`SELECT ... FOR UPDATE`) and read again, so the version
    compared is the one the write lands on. That needs the request in a transaction - turn on
    `ATOMIC_REQUESTS` - and raises `ImproperlyConfigured` without one. A stale tag is refused with
    412, `{"detail", "code": "precondition_failed", "current"}` - `current` the row as `retrieve`
    shows it - and the current `ETag`. A list of tags matches if any one does.

    Five options, each a setting `PRECONDITION_<OPTION>` that a view's `precondition_<option>`
    overrides (None, the default, defers to the setting) - read with `precondition_option(name)`:

    - `ignores_weak_tags` (False) - RFC 9110's strong comparison: a weak tag never matches. True
      ignores a `W/` prefix: the tag names the row's version, not the bytes of one response, and
      compression weakens it on the way out - Django's GZipMiddleware, nginx's gzip - so a client
      echoing the header it got through one would be refused on every write. Reading the version
      from `ConcurrencyTokenField`, which compression never touches, avoids that under the default.
    - `required` (True) - a write without `If-Match` is a 428. False lets it through unchecked; one
      that sends it is still checked.
    - `allows_any` (True) - `If-Match: *` matches any current version. False refuses it with 428:
      the write must name the version it was made against.
    - `missing_row_status` (404) - what a write with `If-Match` gets when the row isn't there. 412,
      RFC 9110's answer, says the condition failed, with `current` null.
    - `not_modified` (True) - `If-None-Match` naming the current version answers a `retrieve` with
      304. False answers in full, still with its `ETag`.

    A successful write sends the token the database left as the new `ETag` - read back, since Django
    doesn't read what a trigger set - and so does an exempt one; a create sends its new row's, and a
    delete none. `perform_update()` reads the row back too, so the body agrees - an override of it
    that doesn't call `super()` leaves the body stale, not the header. Under
    `IdempotencyMixin`, a retry is replayed in `initial()`, before any of this, so it gets its first
    success back rather than a 412 - in either order of the two mixins.

    `ETag` is sent on `retrieve` only, of the reads (`precondition_read_actions`): another detail
    GET shows something else. The tag is the row's version, so a representation that differs per
    user relies on the response's `Vary` (`Cookie`, `Authorization`) to keep a shared cache from
    answering one user's `If-None-Match` with another's page.
    """

    precondition_methods = ("PUT", "PATCH", "DELETE", "POST")
    precondition_exempt_actions = {}
    # {write action: the read action, of this view, whose ETag it names} - see precondition_read_problems().
    precondition_read_from = {}
    # {write action: reason} - a write that asks If-Match with no read handing out its version.
    precondition_unread_actions = {}
    # None is a view that isn't a viewset, whose GET is its only read.
    precondition_read_actions = ("retrieve", None)
    precondition_model = None
    # None defers to the setting - see the docstring, and PRECONDITION_DEFAULTS.
    precondition_ignores_weak_tags = None
    precondition_required = None
    precondition_allows_any = None
    precondition_missing_row_status = None
    precondition_not_modified = None

    # Per request: the row whose version the response carries, and whether get_object() ran the check.
    precondition_instance = None
    precondition_checked = False
    # IdempotencyMixin's own, set when it answers with a replay - declared here too, so a view without
    # that mixin reads it as False.
    idempotent_replayed = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.precondition_exempt_actions = require_exemptions(
            cls.__name__, "precondition_exempt_actions", cls.precondition_exempt_actions, NoPrecondition
        )
        cls.precondition_unread_actions = require_exemptions(
            cls.__name__, "precondition_unread_actions", cls.precondition_unread_actions, NoReadableVersion
        )
        if not isinstance(cls.precondition_read_from, Mapping):
            raise ImproperlyConfigured(
                _("%(owner)s.precondition_read_from takes {write action: read action}, not %(value)r.")
                % {"owner": cls.__name__, "value": cls.precondition_read_from}
            )

    @classmethod
    def get_precondition_model(cls):
        """The model whose rows the view writes: `precondition_model`, else `model`, else the queryset's."""
        queryset = getattr(cls, "queryset", None)
        return cls.precondition_model or getattr(cls, "model", None) or getattr(queryset, "model", None)

    @classmethod
    def precondition_option(cls, name):
        """`name`'s value for this view: its own `precondition_<name>`, else `PRECONDITION_<NAME>`, else the default."""
        own = getattr(cls, f"precondition_{name}")
        if own is not None:
            return own
        return getattr(settings, f"PRECONDITION_{name.upper()}", PRECONDITION_DEFAULTS[name])

    @property
    def precondition_action(self):
        """The viewset action being served - None on a view that isn't a viewset."""
        return getattr(self, "action", None)

    def get_object(self):
        request, action = self.request, self.precondition_action
        if request.method in SAFE_METHODS:
            instance = super().get_object()
            self.precondition_checked = True
            if action in self.precondition_read_actions and has_concurrency_token(instance):
                header, etag = request.META.get("HTTP_IF_NONE_MATCH"), concurrency_etag(instance)
                if header is not None and self.precondition_option("not_modified") and matches(header, etag):
                    raise NotModified(etag)
                self.precondition_instance = instance
            return instance
        asked = requires_precondition(self, request.method, action, getattr(self, "detail", None))
        header = request.META.get("HTTP_IF_MATCH") if asked else None
        try:
            instance = super().get_object()
        except Http404:
            if self._missing_row_fails(header):
                raise PreconditionFailed(None) from None
            raise
        if header is not None:
            instance = self._compared(instance, header)
        elif asked and self.precondition_option("required"):
            raise PreconditionRequired()
        self.precondition_checked = True
        # An exempt write, or one let through without If-Match, still answers with the version it left.
        self.precondition_instance = instance
        return instance

    def _missing_row_fails(self, header):
        """Whether a write gets 412 rather than 404 for a row that isn't there - see `missing_row_status`."""
        status_code = self.precondition_option("missing_row_status")
        return header is not None and status_code == status.HTTP_412_PRECONDITION_FAILED

    def _compared(self, instance, header):
        tags = entity_tags(header, ignores_weak=self.precondition_option("ignores_weak_tags"))
        if "*" in tags and not self.precondition_option("allows_any"):
            raise PreconditionRequired(
                _("This request must name the version it was made against - If-Match: * isn't accepted.")
            )
        instance = self._locked(instance, header)
        if "*" not in tags and concurrency_etag(instance) not in tags:
            raise PreconditionFailed(instance)
        return instance

    def _locked(self, instance, header):
        model = instance.__class__
        # Through the base manager, which joins nothing - Postgres refuses FOR UPDATE on the nullable
        # side of an outer join, which the view's own queryset may make with select_related(). On the
        # database the model's writes are routed to, as a locking read is.
        locking = model._base_manager.select_for_update()
        if not transaction.get_connection(locking.db).in_atomic_block:
            raise ImproperlyConfigured(
                _(
                    "%(view)s compares If-Match with the row it locks, which needs its request served in a "
                    "transaction - turn on ATOMIC_REQUESTS."
                )
                % {"view": type(self).__name__}
            )
        # Read again under the lock: the row get_object() read may have changed before the lock was
        # taken, and a write saves every field it holds.
        try:
            instance.refresh_from_db(from_queryset=locking)
        except model.DoesNotExist:
            if self._missing_row_fails(header):
                raise PreconditionFailed(None) from None
            raise Http404 from None
        # An ETag read with the row (with_concurrency_etags()) was read before the lock: dropped, so a
        # version covering relations is read again now that a change to one has to wait for it.
        instance.__dict__.pop(ETAG_ANNOTATION, None)
        return instance

    def perform_update(self, serializer):
        super().perform_update(serializer)
        # The body shows what the triggers just set - the token, and updated_at beside a version - not
        # what the row was read with.
        serializer.instance.refresh_from_db()

    def _as_retrieve(self):
        view = copy.copy(self)
        view.action = "retrieve"
        return view

    def get_current_serializer_class(self):
        """The serializer class `retrieve` shows a row with - a 412's `current`."""
        return self._as_retrieve().get_serializer_class()

    def get_current_representation(self, instance):
        """The row as `retrieve` shows it - a 412's `current`."""
        return self._as_retrieve().get_serializer(instance).data

    def caller_may_read(self, instance):
        """
        Whether this request's caller may `retrieve` `instance` - a 412 shows `current`, and its ETag, only
        then. The write already passed the gates of the write's own action; the read's may differ (a
        permission per action, a `get_queryset()` narrower for reads), so they're run again here.
        """
        view = self._as_retrieve()
        try:
            view.check_permissions(self.request)
            if not view.filter_queryset(view.get_queryset()).filter(pk=instance.pk).exists():
                return False
            view.check_object_permissions(self.request, instance)
        except (PermissionDenied, NotAuthenticated):
            return False
        return True

    def handle_exception(self, exc):
        if isinstance(exc, NotModified):
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": exc.etag})
        if isinstance(exc, PreconditionRequired | PreconditionFailed):
            # Rendered here rather than by DRF's handler, which would leave out the code and render
            # `current`'s values as error strings.
            data = {"detail": exc.detail, "code": exc.detail.code}
            headers = {}
            if isinstance(exc, PreconditionFailed):
                data["current"] = None
                # A caller who may write the row but not read it gets no more than a row that's gone.
                if exc.instance is not None and self.caller_may_read(exc.instance):
                    data["current"] = self.get_current_representation(exc.instance)
                    headers["ETag"] = concurrency_etag(exc.instance)
            # Rolled back as DRF's handler would - only now: a transaction marked for rollback refuses
            # every query after it, and rendering `current` may need some (a many=True relation).
            set_rollback()
            return Response(data, status=exc.status_code, headers=headers)
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        self._require_precondition_checked(request, response)
        self._require_version_read(request, response)
        instance = self.precondition_instance
        if instance is None and request.method == "POST" and self.precondition_action in ("create", None):
            # A create's row is its serializer's, which DRF's `serializer.data` - the response's data -
            # still carries. Read there rather than remembered from get_serializer(): overriding that
            # flips drf-spectacular onto calling it for every view, with no request to give it.
            serializer = getattr(response.data, "serializer", None)
            instance = getattr(serializer, "instance", None)
        if instance is not None and has_concurrency_token(instance) and status.is_success(response.status_code):
            etag = self._etag_after(request, instance)
            if etag is not None:
                response["ETag"] = etag
        return response

    def _etag_after(self, request, instance):
        if request.method in SAFE_METHODS:
            return concurrency_etag(instance)
        # A write may have changed the row any way it liked - an action's own save(), a delete - and
        # Django doesn't read back what the trigger set, so the token is read again.
        return current_concurrency_etag(instance.__class__, instance.pk)

    def _require_precondition_checked(self, request, response):
        # The check lives in get_object(), and an action needn't call it - a detail=False PATCH on
        # /users/me/, say - so a write that asks If-Match and succeeded without it is refused after the
        # fact, as GuardedFieldsMixin refuses one that skipped its guards. Raised from here it reaches
        # Django's handler, and ATOMIC_REQUESTS - which the lock needs anyway - rolls the write back.
        # A replay under IdempotencyMixin answers with a write that was checked when it was made.
        if self.precondition_checked or self.idempotent_replayed:
            return
        if not status.is_success(response.status_code):
            return
        if not requires_precondition(self, request.method, self.precondition_action, getattr(self, "detail", None)):
            return
        raise ImproperlyConfigured(
            _(
                "%(view)s answered %(method)s %(action)s without comparing If-Match, so its database writes are "
                "rolled back. Fetch the row with self.get_object(), which compares it, or exempt the action in "
                "precondition_exempt_actions."
            )
            % {"view": type(self).__name__, "method": request.method, "action": self.precondition_action}
        )

    def _require_version_read(self, request, response):
        # A read the view names as handing out the version sends its ETag from get_object() - one that
        # answered without it sent none, and a client has nothing to name in If-Match. Refused as a
        # write that skipped the check is; a read has nothing to roll back, but fails as loudly.
        if self.precondition_checked or request.method not in ("GET", "HEAD"):
            return
        if not status.is_success(response.status_code):
            return
        action = self.precondition_action
        if action not in self.precondition_read_actions:
            return
        # A view that isn't a viewset reads one object only where its URL names one - its list doesn't.
        if action is None and (self.lookup_url_kwarg or self.lookup_field) not in self.kwargs:
            return
        model = self.get_precondition_model()
        if model is None or concurrency_field_name(model) is None:
            return
        raise ImproperlyConfigured(
            _(
                "%(view)s answered %(method)s %(action)s without get_object(), so it sent no ETag - and a write "
                "needs one to name in If-Match. Fetch the row with self.get_object(), or take the action out of "
                "precondition_read_actions."
            )
            % {"view": type(self).__name__, "method": request.method, "action": action}
        )


def _declared_serializers(view):
    declared = [view.serializer_class, *getattr(view, "serializer_class_action_map", {}).values()]
    return [serializer for serializer in dict.fromkeys(declared) if serializer is not None]


def _writable_token_problems(view, model):
    name = concurrency_field_name(model)
    return [
        _(
            "%(serializer)s, a serializer of %(view)s, lets a request write %(model)s's concurrency token "
            "%(token)r as %(field)r - make it read-only."
        )
        % {
            "serializer": serializer.__name__,
            "view": view.__name__,
            "model": model._meta.label,
            "token": name,
            "field": field_name,
        }
        for serializer in _declared_serializers(view)
        for field_name, field in serializer().fields.items()
        if not field.read_only and field.source == name
    ]


def _routed_precondition_views(urlconf):
    from isik.django.drf.coverage import routed_actions

    views = dict.fromkeys(routed.view for routed in routed_actions(urlconf))
    return [view for view in views if issubclass(view, PreconditionMixin)]


def precondition_problems(urlconf=None):
    """
    Why the `PreconditionMixin` views `urlconf` routes (every urlconf the project serves by default)
    can't trust the tokens they compare, one sentence each - `[]` when they can. A view is held to it
    when some write it routes asks for `If-Match` - one whose every write is exempt is not - and then
    its model is found, its concurrency token passes `concurrency_token_problems()`, and no serializer the view
    declares (`serializer_class`, `serializer_class_action_map`) lets a request write it. The
    system check `isik.E002` reports them.
    """
    from isik.django.drf.coverage import routed_actions

    # Only a view that asks If-Match of some write it routes compares tokens - one whose every write is
    # exempt uses the mixin to say so, and may write no versioned row at all.
    comparing = dict.fromkeys(
        routed.view
        for routed in routed_actions(urlconf)
        if issubclass(routed.view, PreconditionMixin)
        and requires_precondition(routed.view, routed.method, routed.action, routed.callback.initkwargs.get("detail"))
    )
    problems = []
    for view in comparing:
        model = view.get_precondition_model()
        if model is None:
            problems.append(
                _("%(view)s compares concurrency tokens of no model isik can find - set precondition_model.")
                % {"view": view.__name__}
            )
            continue
        token_problems = concurrency_token_problems(model)
        problems += token_problems or _writable_token_problems(view, model)
    return list(dict.fromkeys(problems))


def precondition_option_problems(urlconf=None):
    """
    Each option of each routed `PreconditionMixin` view that holds a value it can't take, one sentence
    each: the four switches take True or False, `missing_row_status` 404 or 412. The system check
    `isik.E003` reports them.
    """
    problems = []
    for view in _routed_precondition_views(urlconf):
        for name in ("ignores_weak_tags", "required", "allows_any", "not_modified"):
            value = view.precondition_option(name)
            if not isinstance(value, bool):
                problems.append(
                    _(
                        "%(view)s's precondition option %(option)r is %(value)r - PRECONDITION_%(setting)s and "
                        "precondition_%(option)s take True or False."
                    )
                    % {"view": view.__name__, "option": name, "value": value, "setting": name.upper()}
                )
        value = view.precondition_option("missing_row_status")
        if value not in MISSING_ROW_STATUSES:
            problems.append(
                _(
                    "%(view)s's precondition option 'missing_row_status' is %(value)r - "
                    "PRECONDITION_MISSING_ROW_STATUS and precondition_missing_row_status take 404 or 412."
                )
                % {"view": view.__name__, "value": value}
            )
    return problems


def precondition_option_warnings(urlconf=None):
    """
    Each routed `PreconditionMixin` view whose options undo one another, one sentence each: refusing
    `If-Match: *` while letting a write through without `If-Match` protects nothing, since a client
    that would send `*` can leave the header out instead. The system check `isik.W001` reports them.
    """
    return [
        _(
            "%(view)s refuses If-Match: * but lets a write through without If-Match, so a client that would "
            "send * leaves the header out instead - refusing * protects nothing unless If-Match is required."
        )
        % {"view": view.__name__}
        for view in _routed_precondition_views(urlconf)
        if view.precondition_option("allows_any") is False and view.precondition_option("required") is False
    ]


def _named(view, action):
    return f"{view.__name__}.{action}" if action else view.__name__


def _gets(routed, urlconf, view, action):
    return [
        read
        for read in routed
        if read.urlconf == urlconf and read.method == "GET" and read.view is view and read.action == action
    ]


def _version_read(write, routed):
    """
    (the routed GET handing out `write`'s version, or None; a problem with the one declared, or None) -
    `precondition_read_from` first, then a read at the write's own path, then the view's `retrieve` for
    a write to one object.
    """
    view = write.view
    declared = view.precondition_read_from.get(write.action)
    if declared is not None:
        reads = _gets(routed, write.urlconf, view, declared)
        if not reads:
            return None, _(
                "%(write)s reads its version from %(read)r (precondition_read_from), which %(view)s doesn't route "
                "with GET."
            ) % {"write": _named(view, write.action), "read": declared, "view": view.__name__}
        if declared not in view.precondition_read_actions:
            return None, _(
                "%(write)s reads its version from %(read)r (precondition_read_from), which isn't in "
                "precondition_read_actions, so it sends no ETag."
            ) % {"write": _named(view, write.action), "read": declared}
        return reads[0], None
    for read in routed:
        if (read.urlconf, read.route, read.method) == (write.urlconf, write.route, "GET") and (
            issubclass(read.view, PreconditionMixin) and read.action in read.view.precondition_read_actions
        ):
            return read, None
    if write.callback.initkwargs.get("detail"):
        reads = _gets(routed, write.urlconf, view, "retrieve")
        if reads and "retrieve" in view.precondition_read_actions:
            return reads[0], None
    return None, None


def precondition_read_problems(urlconf=None):
    """
    Every routed write that asks `If-Match` with no read handing out the version to send, one sentence
    each - `[]` when each has one. The read is, in order: the action `precondition_read_from` names, of
    the same view; a GET at the write's own path whose action is in its view's
    `precondition_read_actions`; the same view's `retrieve`, for a `detail=True` write. It must be of
    the same model. A write in `precondition_unread_actions` is left out. Paired within one urlconf -
    every urlconf the project serves by default. The system check `isik.E004` reports them.
    """
    from isik.django.drf.coverage import routed_actions

    routed = routed_actions(urlconf)
    problems = []
    for write in routed:
        view = write.view
        if not issubclass(view, PreconditionMixin) or write.action in view.precondition_unread_actions:
            continue
        if not requires_precondition(view, write.method, write.action, write.callback.initkwargs.get("detail")):
            continue
        read, problem = _version_read(write, routed)
        if problem is not None:
            problems.append(problem)
            continue
        if read is None:
            problems.append(
                _(
                    "%(write)s (%(method)s %(route)s) asks If-Match, and nothing routed hands out the version to "
                    "send - no GET of %(route)s sends an ETag, and there's no retrieve to read it from. Route one, "
                    "name it in precondition_read_from, or exempt the write in precondition_unread_actions."
                )
                % {"write": _named(view, write.action), "method": write.method, "route": write.route}
            )
            continue
        model, read_model = view.get_precondition_model(), read.view.get_precondition_model()
        # A model either can't be found is isik.E002's to report.
        if None not in (model, read_model) and read_model is not model:
            problems.append(
                _(
                    "%(write)s (%(method)s %(route)s) reads its version from %(read)s, which hands out a version "
                    "of %(read_model)s, not of %(model)s."
                )
                % {
                    "write": _named(view, write.action),
                    "method": write.method,
                    "route": write.route,
                    "read": _named(read.view, read.action),
                    "read_model": read_model._meta.label,
                    "model": model._meta.label,
                }
            )
    return problems
