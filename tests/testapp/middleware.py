"""A HistoryMiddleware subclass, only to prove history_middleware_installed() detects subclasses
too, not just the exact dotted path."""

from pghistory.middleware import HistoryMiddleware


class CustomHistoryMiddleware(HistoryMiddleware):
    pass
