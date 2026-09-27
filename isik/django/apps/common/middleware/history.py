import pghistory
from pghistory import config
from pghistory.middleware import HistoryMiddleware

from isik.django.apps.common.db.history import _open_history_context


class HistoryContextMiddleware(HistoryMiddleware):
    """
    pghistory's `HistoryMiddleware`, plus a way to read the context it opens: `open_history_context()`
    returns it for as long as the request is being served. pghistory itself publishes no reader - the
    one it keeps is a private thread-local.

    A drop-in replacement, and subclassed the same way - override `get_context()` to add keys.
    """

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
