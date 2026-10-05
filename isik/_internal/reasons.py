"""
How isik's own opt-outs insist on saying why - the few whose default isn't obvious to turn off.

A single opt-out (`exempt_from_registry`, `@writes_no_guarded_fields`) is an exemption type's
(`isik.common.utils.exemptions`), so it is held to that type's floor and listed by its rule. A
`{name: reason}` map needs no floor - the policy or field and the name already give the context - but
a reason there is still more than blank, or a placeholder standing in for one.
"""

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption


# What gets typed where a reason should be, by someone who has none yet.
PLACEHOLDERS = frozenset({"n/a", "na", "none", "tbd", "x", "-", ".", "?", "..."})


def is_reason(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in PLACEHOLDERS


def require_exemption(owner, attribute, value, exemption_type):
    """
    The exemption `value` declares, as an `exemption_type` - its reason as a `str`, or one already made -
    or None for `False`/`None`, which don't opt out. `True` says to opt out, but not why.
    """
    if value is None or value is False:
        return None
    if isinstance(value, exemption_type):
        return value
    if isinstance(value, str) and not isinstance(value, Exemption):
        try:
            return exemption_type(reason=value)
        except ValueError as error:
            raise ImproperlyConfigured(
                _("%(owner)s.%(attribute)s - %(error)s") % {"owner": owner, "attribute": attribute, "error": error}
            ) from error
    raise ImproperlyConfigured(
        _("%(owner)s.%(attribute)s takes a %(type)s, or its reason as a str, not %(value)r.")
        % {"owner": owner, "attribute": attribute, "type": exemption_type.__name__, "value": value}
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
