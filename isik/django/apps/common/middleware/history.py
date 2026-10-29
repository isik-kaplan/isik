import uuid

import pghistory
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from pghistory import config
from pghistory.middleware import HistoryMiddleware

from isik._internal.translation import gettext as _
from isik.django.apps.common.db.history import _open_history_context


# The keys a project may add to every request's context by naming them in HISTORY_CONTEXT_KEYS.
OPTIONAL_CONTEXT_KEYS = ("version", "build", "request_id")
# The keys whose value a setting gives, the same for every request - required once the key is named.
FROM_SETTINGS = {"version": "HISTORY_CONTEXT_VERSION", "build": "HISTORY_CONTEXT_BUILD"}
# Where a request's id is read from, unless HISTORY_REQUEST_ID_HEADER names another.
REQUEST_ID_HEADER = "X-Request-ID"


class HistoryContextMiddleware(HistoryMiddleware):
    """
    pghistory's `HistoryMiddleware`, plus a way to read the context it opens: `open_history_context()`
    returns it for as long as the request is being served. pghistory itself publishes no reader - the
    one it keeps is a private thread-local.

    A drop-in replacement, and subclassed the same way - override `get_context()` to add keys.

    It also offers keys a project picks with `HISTORY_CONTEXT_KEYS` - none by default, so the context
    is pghistory's own:

    - `"version"` - `HISTORY_CONTEXT_VERSION`, the version serving the request, so a row says which
      release wrote it. Required once named.
    - `"build"` - `HISTORY_CONTEXT_BUILD`, the build itself - a commit, say - for when one version is many
      builds. Required once named.
    - `"request_id"` - the request's `HISTORY_REQUEST_ID_HEADER` (`X-Request-ID` unless set), as a
      proxy or a client sent it, else one made here - so a row can be found again from a log line.
    """

    def __init__(self, get_response):
        super().__init__(get_response)
        self.context_keys = tuple(getattr(settings, "HISTORY_CONTEXT_KEYS", ()))
        unknown = sorted(set(self.context_keys) - set(OPTIONAL_CONTEXT_KEYS))
        if unknown:
            raise ImproperlyConfigured(
                _("HISTORY_CONTEXT_KEYS names %(unknown)s - it takes %(known)s.")
                % {"unknown": ", ".join(unknown), "known": ", ".join(OPTIONAL_CONTEXT_KEYS)}
            )
        for key, setting in FROM_SETTINGS.items():
            if key in self.context_keys and not getattr(settings, setting, None):
                raise ImproperlyConfigured(
                    _("HISTORY_CONTEXT_KEYS names %(key)s, and %(setting)s isn't set.")
                    % {"key": key, "setting": setting}
                )

    def get_context(self, request):
        context = super().get_context(request)
        for key, setting in FROM_SETTINGS.items():
            if key in self.context_keys:
                context[key] = getattr(settings, setting)
        if "request_id" in self.context_keys:
            header = getattr(settings, "HISTORY_REQUEST_ID_HEADER", REQUEST_ID_HEADER)
            context["request_id"] = request.headers.get(header) or uuid.uuid4().hex
        return context

    def __call__(self, request):
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
