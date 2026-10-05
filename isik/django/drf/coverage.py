"""
Which routed DRF views carry a request-level rule, which are exempt from it and why, and which say
nothing at all - the half of "every view must do X" that a project enforces in its own check or test:

    def test_every_routed_action_is_behind_the_setup_gate():
        uncovered = [entry for entry in request_policy_coverage(OrganizationIsSetUp) if entry.is_uncovered]
        assert uncovered == []

`routed_actions()` is the walk over DRF views and `routed_views()` the walk over every view;
`request_policy_coverage()` (here) and `idempotency_coverage()` (`isik.django.apps.idempotency.coverage`)
answer it for one rule.
"""

from dataclasses import dataclass
from enum import StrEnum

from django.urls import URLResolver, get_resolver

from isik._internal.reasons import is_reason
from isik._internal.translation import gettext as _
from isik.django.drf.viewsets.request_policies import RequestPoliciesMixin


class ViewKind(StrEnum):
    DRF = "drf"
    CLASS = "class"
    FUNCTION = "function"


@dataclass(frozen=True)
class RoutedAction:
    """
    One method a routed view answers. `action` is the viewset action, None anywhere else. `kind` says
    what answers it: a DRF view (`view` is its class), a Django class-based view (`view` is its class),
    or a function view (`view` is the function, and `method` is None - which methods a function
    answers can't be read from it).
    """

    route: str
    view: type
    method: str | None
    action: str | None
    kind: ViewKind = ViewKind.DRF


class CoverageStatus(StrEnum):
    COVERED = "covered"
    EXEMPT = "exempt"
    UNCOVERED = "uncovered"


@dataclass(frozen=True)
class Coverage:
    """Whether `routed` carries a rule - and, when it's exempt, the reason it gave."""

    routed: RoutedAction
    status: CoverageStatus
    reason: str | None = None

    @property
    def is_uncovered(self):
        return self.status is CoverageStatus.UNCOVERED


def _walk(patterns, prefix):
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, route)
        else:  # a URLPattern - Django's urlconfs hold nothing else
            yield route, pattern.callback


def _handlers(cls):
    """The methods a class-based view implements. OPTIONS is answered for every view and changes nothing."""
    return [method.upper() for method in cls.http_method_names if method != "options" and hasattr(cls, method)]


def _answers(view):
    """[(METHOD, action)] a routed DRF view answers - a viewset's from its router, an APIView's from its handlers."""
    actions = getattr(view, "actions", None)
    if actions is not None:
        return [(method.upper(), action) for method, action in actions.items()]
    return [(method, None) for method in _handlers(view.cls)]


def routed_views(urlconf=None):
    """
    Every method of every view `urlconf` routes (the project's `ROOT_URLCONF` by default), in route
    order: a DRF view's as `routed_actions()` gives them, a Django class-based view's one per method it
    implements, and a function view once, with `method=None` - see `RoutedAction`.
    """
    found = []
    for route, view in _walk(get_resolver(urlconf).url_patterns, ""):
        if getattr(view, "cls", None) is not None:
            for method, action in _answers(view):
                found.append(RoutedAction(route, view.cls, method, action))
        elif getattr(view, "view_class", None) is not None:
            for method in _handlers(view.view_class):
                found.append(RoutedAction(route, view.view_class, method, None, ViewKind.CLASS))
        else:
            found.append(RoutedAction(route, view, None, None, ViewKind.FUNCTION))
    return found


def routed_actions(urlconf=None):
    """
    Every method of every DRF view `urlconf` routes (the project's `ROOT_URLCONF` by default), in route
    order - `routed_views()` without the views that aren't DRF's. OPTIONS is left out: DRF answers it
    for every view, and it changes nothing.
    """
    return [routed for routed in routed_views(urlconf) if routed.kind is ViewKind.DRF]


def _plain_view_coverage(routed, plain_views):
    verdict = plain_views(routed)
    if verdict is True:
        return Coverage(routed, CoverageStatus.COVERED)
    if verdict is False:
        return Coverage(routed, CoverageStatus.UNCOVERED)
    if is_reason(verdict):
        return Coverage(routed, CoverageStatus.EXEMPT, verdict)
    raise TypeError(
        _("plain_views() answers True, False or a reason, not %(verdict)r for %(route)s.")
        % {"verdict": verdict, "route": routed.route}
    )


def request_policy_coverage(policy, urlconf=None, *, plain_views=None):
    """
    `Coverage` of `policy` (a `RequestPolicy` class) for every routed DRF action - see `routed_actions()`.

    A view that isn't DRF's can't carry `RequestPoliciesMixin`, so isik can't tell whether it holds to
    `policy`. Pass `plain_views(routed)` to say, and those views join the report: it answers `True`
    (covered), a reason (exempt), or `False` (uncovered), for each entry of `routed_views()` that isn't
    DRF's - a function view once, with `method=None`. Without it they're left out.
    """
    report = []
    for routed in routed_views(urlconf):
        view = routed.view
        if routed.kind is not ViewKind.DRF:
            if plain_views is not None:
                report.append(_plain_view_coverage(routed, plain_views))
        elif not issubclass(view, RequestPoliciesMixin) or policy not in view.request_policies:
            report.append(Coverage(routed, CoverageStatus.UNCOVERED))
        elif routed.action in (exemptions := view.request_policy_exemptions(policy)):
            report.append(Coverage(routed, CoverageStatus.EXEMPT, exemptions[routed.action]))
        else:
            report.append(Coverage(routed, CoverageStatus.COVERED))
    return report
