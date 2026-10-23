"""
How isik's own opt-outs insist on saying why - the few whose default isn't obvious to turn off.

Every opt-out is an exemption type's (`isik.common.utils.exemptions`), so it is held to that type's
floor and listed by its rule - a single one (`exempt_from_registry`) and each value of a
`{name: reason}` map (`precondition_exempt_actions`) alike. Where a value is written, it's written as
the type - `NoPrecondition(reason="...")`, never a bare string: at a call site a bare string reads as a
value to be used, an exception message say, rather than the reason a rule is skipped. A factory whose
own call names the rule (`@writes_no_guarded_fields("...")`) takes the reason as text - there's nothing
to mistake it for there.
"""

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption


# What gets typed where a reason should be, by someone who has none yet.
PLACEHOLDERS = frozenset({"n/a", "na", "none", "tbd", "x", "-", ".", "?", "..."})


def is_reason(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in PLACEHOLDERS


def _refusal(owner, attribute, value, exemption_type, name=None):
    where = {"owner": owner, "attribute": attribute, "type": exemption_type.__name__, "name": name, "value": value}
    if isinstance(value, str) and not isinstance(value, Exemption):
        message = (
            _("%(owner)s.%(attribute)s takes a %(type)s(reason=...) for %(name)s, not a bare string.")
            if name is not None
            else _("%(owner)s.%(attribute)s takes a %(type)s(reason=...), not a bare string.")
        )
    else:
        message = (
            _("%(owner)s.%(attribute)s takes a %(type)s(reason=...) for %(name)s, not %(value)r.")
            if name is not None
            else _("%(owner)s.%(attribute)s takes a %(type)s(reason=...), not %(value)r.")
        )
    return ImproperlyConfigured(message % where)


def require_exemption(owner, attribute, value, exemption_type):
    """
    The exemption `value` declares - an `exemption_type` - or None for `False`/`None`, which don't opt
    out. Anything else is refused: `True` says to opt out but not why, and a bare string says why
    without saying it's a reason.
    """
    if value is None or value is False:
        return None
    if isinstance(value, exemption_type):
        return value
    raise _refusal(owner, attribute, value, exemption_type)


def require_exemptions(owner, attribute, exemptions, exemption_type):
    """Refuses `exemptions` unless it's a `{name: exemption_type(reason=...)}` mapping, and returns it."""
    if not isinstance(exemptions, Mapping):
        raise ImproperlyConfigured(
            _("%(owner)s.%(attribute)s takes {name: %(type)s(reason=...)}, not %(value)r.")
            % {"owner": owner, "attribute": attribute, "type": exemption_type.__name__, "value": exemptions}
        )
    for name, value in exemptions.items():
        if not isinstance(value, exemption_type):
            raise _refusal(owner, attribute, value, exemption_type, name)
    return exemptions


def made_exemption(owner, attribute, reason, exemption_type):
    """
    `reason` as an `exemption_type`, for a factory whose own call names the rule - `reason` is text, or
    one already made. A reason under the type's floor is refused naming where it was given.
    """
    if isinstance(reason, exemption_type):
        return reason
    try:
        return exemption_type(reason=reason)
    except ValueError as error:
        raise ImproperlyConfigured(
            _("%(owner)s.%(attribute)s - %(error)s") % {"owner": owner, "attribute": attribute, "error": error}
        ) from error
