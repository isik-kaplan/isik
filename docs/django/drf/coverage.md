# coverage

`isik.django.drf.coverage` - which routed DRF views carry a request-level rule, which are exempt from
it and why, and which say nothing at all. The same shape as a field that either has a comment or says
`NoComment("why")`: the opt-out is explicit, and something walks everything to find what is neither.
isik provides the walk; which rules are mandatory is the project's to decide, in its own system
check or test.

```python
from isik.django.drf.coverage import request_policy_coverage
from isik.django.apps.idempotency.coverage import idempotency_coverage


def test_every_routed_action_is_behind_the_setup_gate():
    uncovered = [entry for entry in request_policy_coverage(OrganizationIsSetUp) if entry.is_uncovered]
    assert uncovered == []


def test_every_post_honors_an_idempotency_key():
    assert [entry for entry in idempotency_coverage() if entry.is_uncovered] == []
```

## routed_actions(urlconf=None)

Every method of every DRF view a urlconf routes, in route order, as
`RoutedAction(route, view, method, action, kind, urlconf, callback)`. `callback` is what the urlconf
routes to - `as_view()`'s function, carrying whatever a decorator around it in the urlconf set on it
(`csrf_exempt(...)`, `transaction.non_atomic_requests(...)`), so a check reading those marks needs no
walk of its own. A viewset contributes one per method its router maps,
with `action` set; a plain `APIView` one per method it implements, with `action=None`. Plain Django
views aren't DRF's and are left out, as is OPTIONS, which DRF answers for every view.

## routed_views(urlconf=None)

Every view a urlconf routes, DRF's or not, through nested `include()`s, with `kind` saying what
answers each entry:

- `ViewKind.DRF` - as `routed_actions()` gives them.
- `ViewKind.CLASS` - a Django class-based view, one entry per method it implements (`post`, `put`,
  ...), with `view` its class.
- `ViewKind.FUNCTION` - a function view, once, with `view` the function and `method=None`: which
  methods a function answers can't be read from it, so a project classifies it by hand.

`routed_actions()` is this without the views that aren't DRF's.

`urlconf` can be one urlconf, by dotted name or as a module, or several in a list or tuple. By
default it's every urlconf the project serves, from `project_urlconfs()` in
`isik.django.apps.common.urlconfs`: `ROOT_URLCONF`, then each host's urlconf when django-hosts is
installed and `ROOT_HOSTCONF` is set. isik doesn't depend on django-hosts. Each urlconf is walked once,
and each entry's `urlconf` names the one that routes it, since under django-hosts the same route can
appear in several.

## request_policy_coverage(policy, urlconf=None, *, plain_views=None)

A `Coverage(routed, status, reason)` per routed action: `covered` when the view runs `policy`,
`exempt` (with the reason it gave) when the action is in the policy's exemptions, `uncovered`
otherwise. `entry.is_uncovered` for the ones to fail on.

A view that isn't DRF's can't carry `RequestPoliciesMixin`. Pass `plain_views(routed)` to judge those
views yourself, and they join the report. It answers `True` (covered), a reason (exempt) or `False`
(uncovered) for each non-DRF entry of `routed_views()`. Without it they're left out. With it, one
closed-world test covers everything routed:

```python
GATED_VIEWS = {EmailAddView, PasswordSetView}


def gate(routed):
    if routed.view is health_check:
        return "answers load balancers, which never sign in"
    return routed.view in GATED_VIEWS or routed.method == "GET"


def test_every_routed_write_is_behind_the_reauthentication_gate():
    report = request_policy_coverage(RecentlyAuthenticated, plain_views=gate)
    assert [entry for entry in report if entry.is_uncovered] == []
```

## idempotency_coverage(urlconf=None, methods=("POST",))

The same for idempotency keys, over the routed actions answering one of `methods`: `covered` when
the view honors a key for that method, `exempt` when the action is in `idempotency_exempt_actions`,
`uncovered` otherwise - a view with `IdempotencyMixin` whose `idempotent_methods` leave the method
out included.
