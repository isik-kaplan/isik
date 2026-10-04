"""
How isik's own opt-outs insist on saying why - the few whose default isn't obvious to turn off.

A reason is any non-blank `str`: a plain one, or a project's own `DeclaredString` sentinel holding itself
to a stricter rule (`isik.common.utils.declared_string`). isik checks only that one was given.
"""

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured

from isik._internal.translation import gettext as _


def is_reason(value):
    return isinstance(value, str) and bool(value.strip())


def require_reason(owner, attribute, value):
    """Refuses a truthy `value` that isn't a reason - `True` says to opt out, but not why."""
    if value and not is_reason(value):
        raise ImproperlyConfigured(
            _("%(owner)s.%(attribute)s needs a reason rather than %(value)r - say why, as a string.")
            % {"owner": owner, "attribute": attribute, "value": value}
        )


def require_reasons(owner, attribute, reasons):
    """Refuses `reasons` unless it's a `{name: reason}` mapping with a reason for every name."""
    if not isinstance(reasons, Mapping):
        raise ImproperlyConfigured(
            _("%(owner)s.%(attribute)s takes {name: reason}, not %(value)r.")
            % {"owner": owner, "attribute": attribute, "value": reasons}
        )
    for name, reason in reasons.items():
        if not is_reason(reason):
            raise ImproperlyConfigured(
                _("%(owner)s.%(attribute)s needs a reason for %(name)s, not %(reason)r.")
                % {"owner": owner, "attribute": attribute, "name": name, "reason": reason}
            )
