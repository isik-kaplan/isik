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

Every method of every DRF view a urlconf routes (`ROOT_URLCONF` by default), in route order, as
`RoutedAction(route, view, method, action)`. A viewset contributes one per method its router maps,
with `action` set; a plain `APIView` one per method it implements, with `action=None`. Plain Django
views aren't DRF's and are left out, as is OPTIONS, which DRF answers for every view.

## request_policy_coverage(policy, urlconf=None)

A `Coverage(routed, status, reason)` per routed action: `covered` when the view runs `policy`,
`exempt` (with the reason it gave) when the action is in the policy's exemptions, `uncovered`
otherwise. `entry.is_uncovered` for the ones to fail on.

## idempotency_coverage(urlconf=None, methods=("POST",))

The same for idempotency keys, over the routed actions answering one of `methods`: `covered` when
the view honors a key for that method, `exempt` when the action is in `idempotency_exempt_actions`,
`uncovered` otherwise - a view with `IdempotencyMixin` whose `idempotent_methods` leave the method
out included.
