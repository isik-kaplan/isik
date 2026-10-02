"""
Which routed DRF views carry a request-level rule, which are exempt from it and why, and which say
nothing at all - the half of "every view must do X" that a project enforces in its own check or test:

    def test_every_routed_action_is_behind_the_setup_gate():
        uncovered = [entry for entry in request_policy_coverage(OrganizationIsSetUp) if entry.is_uncovered]
        assert uncovered == []

`routed_actions()` is the walk; `request_policy_coverage()` (here) and `idempotency_coverage()`
(`isik.django.apps.idempotency.coverage`) answer it for one rule.
"""

from dataclasses import dataclass
from enum import StrEnum

from django.urls import URLResolver, get_resolver

from isik.django.drf.viewsets.request_policies import RequestPoliciesMixin


@dataclass(frozen=True)
class RoutedAction:
    """One method a routed DRF view answers: `action` is the viewset action, None on a plain APIView."""

    route: str
    view: type
    method: str
    action: str | None


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


def _answers(view):
    """{METHOD: action} a view function answers - a viewset's from its router, an APIView's from its handlers."""
    actions = getattr(view, "actions", None)
    if actions is not None:
        return {method.upper(): action for method, action in actions.items()}
    return {
        method.upper(): None
        for method in view.cls.http_method_names
        if method != "options" and hasattr(view.cls, method)
    }


def routed_actions(urlconf=None):
    """
    Every method of every DRF view `urlconf` routes (the project's `ROOT_URLCONF` by default), in route
    order. A plain Django view isn't DRF's, and is left out. OPTIONS is left out: DRF answers it for
    every view, and it changes nothing.
    """
    found = []
    for route, view in _walk(get_resolver(urlconf).url_patterns, ""):
        if getattr(view, "cls", None) is None:
            continue
        for method, action in _answers(view).items():
            found.append(RoutedAction(route=route, view=view.cls, method=method, action=action))
    return found


def request_policy_coverage(policy, urlconf=None):
    """`Coverage` of `policy` (a `RequestPolicy` class) for every routed action - see `routed_actions()`."""
    report = []
    for routed in routed_actions(urlconf):
        view = routed.view
        if not issubclass(view, RequestPoliciesMixin) or policy not in view.request_policies:
            report.append(Coverage(routed, CoverageStatus.UNCOVERED))
        elif routed.action in (exemptions := view.request_policy_exemptions(policy)):
            report.append(Coverage(routed, CoverageStatus.EXEMPT, exemptions[routed.action]))
        else:
            report.append(Coverage(routed, CoverageStatus.COVERED))
    return report
