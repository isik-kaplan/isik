import pghistory
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from pghistory import config
from pghistory.middleware import HistoryMiddleware

from isik._internal.translation import gettext as _
from isik.django.apps.common.db.context_columns import UUID_PATTERN
from isik.django.apps.common.db.history import _open_history_context
from isik.django.apps.common.db.models import uuid7
from isik.django.conf import django_setting


# The keys a project may add to every request's context by naming them in ISIK['DJANGO']['HISTORY']['CONTEXT_KEYS'].
OPTIONAL_CONTEXT_KEYS = ("version", "build", "request_id", "correlation_id", "trace_id")
# The keys whose value a setting gives, the same for every request - required once the key is named.
FROM_SETTINGS = {"version": "CONTEXT_VERSION", "build": "CONTEXT_BUILD"}
# Where a request's correlation id is read from and answered under, unless
# ISIK['DJANGO']['HISTORY']['REQUEST_ID_HEADER'] names another.
REQUEST_ID_HEADER = "X-Request-ID"
# Where a Sentry trace arrives: `<trace id>-<span id>[-<sampled>]`.
TRACE_HEADER = "sentry-trace"


def is_uuid(value):
    """
    Whether `value` is a UUID and nothing else - hyphenated, or 32 hex digits: the default id check, and what a
    `"uuid"` context column holds (`AddContextColumn`).
    """
    return UUID_PATTERN.fullmatch(value) is not None


def well_formed(value):
    """`value` - an id a client or proxy sent - when ISIK['DJANGO']['HISTORY']['REQUEST_ID_VALID'] takes it, or None."""
    if not value:
        return None
    valid = django_setting("HISTORY", "REQUEST_ID_VALID") or is_uuid
    valid = import_string(valid) if isinstance(valid, str) else valid
    return value if valid(value) else None


class HistoryContextMiddleware(HistoryMiddleware):
    """
    pghistory's `HistoryMiddleware`, plus a way to read the context it opens: `open_history_context()`
    returns it for as long as the request is being served. pghistory itself publishes no reader - the
    one it keeps is a private thread-local.

    A drop-in replacement, and subclassed the same way - override `get_context()` to add keys.

    It also offers keys a project picks with `ISIK['DJANGO']['HISTORY']['CONTEXT_KEYS']` - none by default, so the
    context is pghistory's own:

    - `"version"` - `ISIK['DJANGO']['HISTORY']['CONTEXT_VERSION']`, the version serving the request, so a row says which
      release wrote it. Required once named.
    - `"build"` - `ISIK['DJANGO']['HISTORY']['CONTEXT_BUILD']`, the build itself - a commit, say - for when one version
      is many builds. Required once named.
    - `"request_id"` - this request's own id, made here for every request and never read from it: each request
      leaves rows apart from every other's.
    - `"correlation_id"` - the id of what the request is part of: the `REQUEST_ID_HEADER` (`X-Request-ID` unless
      set) a proxy or a calling server sent, once `REQUEST_ID_VALID` takes it, else the request's own id.
    - `"trace_id"` - the Sentry trace the request belongs to, read off its `sentry-trace` header - or None.

    Every request carries `request.request_id` and `request.correlation_id`, whatever the context holds, and its
    response answers with the correlation id under `REQUEST_ID_HEADER` - unless `RETURN_REQUEST_ID` is False.
    """

    def __init__(self, get_response):
        super().__init__(get_response)
        self.context_keys = tuple(django_setting("HISTORY", "CONTEXT_KEYS", ()))
        unknown = sorted(set(self.context_keys) - set(OPTIONAL_CONTEXT_KEYS))
        if unknown:
            raise ImproperlyConfigured(
                _("ISIK['DJANGO']['HISTORY']['CONTEXT_KEYS'] names %(unknown)s - it takes %(known)s.")
                % {"unknown": ", ".join(unknown), "known": ", ".join(OPTIONAL_CONTEXT_KEYS)}
            )
        for key, setting in FROM_SETTINGS.items():
            if key in self.context_keys and not django_setting("HISTORY", setting):
                raise ImproperlyConfigured(
                    _(
                        "ISIK['DJANGO']['HISTORY']['CONTEXT_KEYS'] names %(key)s, and "
                        "ISIK['DJANGO']['HISTORY']['%(setting)s'] isn't set."
                    )
                    % {"key": key, "setting": setting}
                )

    def get_context(self, request):
        context = super().get_context(request)
        for key, setting in FROM_SETTINGS.items():
            if key in self.context_keys:
                context[key] = django_setting("HISTORY", setting)
        self.identify(request)
        ids = {
            "request_id": request.request_id,
            "correlation_id": request.correlation_id,
            "trace_id": well_formed(request.headers.get(TRACE_HEADER, "").split("-")[0]),
        }
        context.update({key: value for key, value in ids.items() if key in self.context_keys})
        return context

    @staticmethod
    def identify(request):
        """
        Gives `request` its `request_id` and `correlation_id`, unless it has them - so `get_context()` answers for a
        request that hasn't been through `__call__`, such as one a test builds.
        """
        if not hasattr(request, "request_id"):
            header = django_setting("HISTORY", "REQUEST_ID_HEADER", REQUEST_ID_HEADER)
            request.request_id = str(uuid7())
            request.correlation_id = well_formed(request.headers.get(header)) or request.request_id

    def __call__(self, request):
        self.identify(request)
        response = self._served(request)
        if django_setting("HISTORY", "RETURN_REQUEST_ID", True):
            response[django_setting("HISTORY", "REQUEST_ID_HEADER", REQUEST_ID_HEADER)] = request.correlation_id
        return response

    def _served(self, request):
        if request.method not in config.middleware_methods():
            return super().__call__(request)
        # Opened here, empty, so the dict kept is the one pghistory goes on adding to: the parent's
        # own `pghistory.context(**self.get_context(request))` joins this one rather than opening
        # its own, as would a `request.user` DRF sets later.
        with pghistory.context() as opened:
            token = _open_history_context.set(opened.metadata)
            try:
                return super().__call__(request)
            finally:
                _open_history_context.reset(token)
