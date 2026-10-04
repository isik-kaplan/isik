"""idempotency_coverage() - which routed state-changing actions honor an idempotency key."""

from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.drf.coverage import Coverage, CoverageStatus, routed_actions


def idempotency_coverage(urlconf=None, methods=IdempotencyMixin.idempotent_methods):
    """
    `Coverage` of idempotency keys for every routed action answering one of `methods` - POST by
    default, what `IdempotencyMixin` covers. Covered when the view honors a key for that method,
    exempt (with its reason) when the action is in `idempotency_exempt_actions`, uncovered otherwise,
    including a view with the mixin whose `idempotent_methods` leave that method out.
    """
    report = []
    for routed in routed_actions(urlconf):
        if routed.method not in methods:
            continue
        view = routed.view
        if not issubclass(view, IdempotencyMixin) or routed.method not in view.idempotent_methods:
            report.append(Coverage(routed, CoverageStatus.UNCOVERED))
        elif routed.action in view.idempotency_exempt_actions:
            report.append(Coverage(routed, CoverageStatus.EXEMPT, view.idempotency_exempt_actions[routed.action]))
        else:
            report.append(Coverage(routed, CoverageStatus.COVERED))
    return report
