"""
Force sources - who or what forces a value on someone, and the policy that goes with it. A project declares
its own, once, where it declares its kinds and features:

    STAFF = declare_force_source("staff", label=_("Staff"), reason_floor=40, shows_reason_to_holder=True)
    REVIEW = declare_force_source("account_review", label=_("Account under review"), operator_may_release=False)

Each force records its source's name and a reason; what a source allows lives on the source, not on each
row - a source that forced three things never wants one releasable and the others not:

- `operator_may_release` - whether staff may lift what it forced, or only the source itself may.
- `reason_floor` - how long a reason has to be: an operator typing a justification needs one worth reading,
  an automated source's fixed reason doesn't.
- `shows_reason_to_holder` - whether the person it was forced on may read the reason; an internal one
  ("account under review") stays with staff.

Notifications' forceable subscriptions and the features app's overrideable settings both take these.
"""

from isik._internal.translation import gettext as _


class ForceSource(str):
    """
    A declared force source's name - what `declare_force_source()` returns - carrying its policy (see the module's
    docstring). A force's row stores the name, so the row's column compares equal to the declaration. Two compare
    by name alone, so one is only ever made by `declare_force_source()`, which refuses a name declared twice.
    """

    def __new__(cls, name, *, label=None, operator_may_release=True, reason_floor=40, shows_reason_to_holder=False):
        declared = super().__new__(cls, name)
        declared.label, declared.operator_may_release = label, operator_may_release
        declared.reason_floor, declared.shows_reason_to_holder = reason_floor, shows_reason_to_holder
        return declared

    @property
    def name(self):
        return str.__str__(self)


class ForceRefused(ValueError):
    """Why a force or a release can't be made - an unknown source, a reason too short, a source staff can't lift."""


# {name: ForceSource}, in the order declared.
_declared = {}


def declare_force_source(name, *, label=None, operator_may_release=True, reason_floor=40, shows_reason_to_holder=False):
    """Declares the force source `name` and returns it. A name declared twice is refused."""
    if name in _declared:
        raise ValueError(_("A force source called %(name)s is declared already.") % {"name": name})
    source = ForceSource(
        name,
        label=label,
        operator_may_release=operator_may_release,
        reason_floor=reason_floor,
        shows_reason_to_holder=shows_reason_to_holder,
    )
    _declared[name] = source
    return source


def force_sources():
    """{name: ForceSource} - every declared source."""
    return dict(_declared)


def get_force_source(source):
    """The declared source `source` names (a `ForceSource` or its name). `ForceRefused` for one that isn't declared."""
    name = str(source)
    try:
        return _declared[name]
    except KeyError:
        raise ForceRefused(_("No force source is declared as %(name)s.") % {"name": name}) from None


def checked_force(source, reason):
    """The declared `source`, once `reason` reaches its floor - `ForceRefused` otherwise."""
    declared = get_force_source(source)
    if len((reason or "").strip()) < declared.reason_floor:
        raise ForceRefused(
            _("A force by %(source)s needs a reason of at least %(floor)s characters.")
            % {"source": _shown(declared), "floor": declared.reason_floor}
        )
    return declared


def checked_release(source, *, by_operator):
    """The declared `source`, once its force may be lifted - by staff only where it allows it."""
    declared = get_force_source(source)
    if by_operator and not declared.operator_may_release:
        raise ForceRefused(
            _("What %(source)s forced can only be lifted by it, not by staff.") % {"source": _shown(declared)}
        )
    return declared


def _shown(declared):
    """What a refusal calls `declared`: its label, where it has one."""
    return declared.label or declared.name
