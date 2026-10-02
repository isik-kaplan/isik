# request_policies

A rule about the *request* rather than about a model surface - "this organization is set up", "this
account has its second factor" - applied to any DRF view: a model viewset, a `ViewSet` over a
third-party library's tokens, a plain `APIView`. Put such a rule on the model viewset base instead,
and every view that isn't one sits outside it without anything saying so.

## RequestPolicy

```python
from isik.django.drf.viewsets import RequestPolicy

class OrganizationIsSetUp(RequestPolicy):
    message = _("This organization is still being set up.")
    code = "organization_not_set_up"
    exemptions_attribute = "setup_gate_exempt_actions"

    def allows(self, request, view):
        return request.tenant.is_set_up


class SecondFactorSatisfied(RequestPolicy):
    message = _("Set up a second factor first.")
    code = "second_factor_required"

    def allows(self, request, view):
        return not request.user.is_authenticated or request.user.has_second_factor

    def refused(self, request, view, response):
        # DRF renders a permission's message, never anything else - a client told which policy
        # refused it can redirect to setup instead of showing "forbidden".
        response["X-Second-Factor-Required"] = "1"
```

- `allows(request, view)` - whether the request may go on.
- `message`/`code` - the refusal: a 403, or a 401 for an unauthenticated request, as DRF decides for
  any permission.
- `refused(request, view, response)` - adds to the refusal's response. Nothing by default.
- `exemptions_attribute` - the view attribute naming this policy's exempt actions.
  `<policy_name>_exempt_actions` (`second_factor_satisfied_exempt_actions`) unless set.

## RequestPoliciesMixin

```python
class AccessTokenViewSet(RequestPoliciesMixin, viewsets.ViewSet):
    request_policies = [OrganizationIsSetUp, SecondFactorSatisfied]
    setup_gate_exempt_actions = {"me": "answers who is asking, which a blocked caller needs to learn why"}
```

- Runs in `check_permissions()`, after the view's own permissions - so `get_permissions()`
  overrides can't drop a policy by accident, and a policy refuses before anything later in the
  request runs (an idempotency key isn't claimed by a refused request).
- The first policy that doesn't allow the request refuses it.
- An exemption is `{action: reason}` under the policy's exemptions attribute, refused at
  class-definition time without a reason for every action.
- `BaseViewSet` and so `BaseModelViewSet` carry it - set `request_policies` on your project's base.

Which views carry a policy, which are exempt and why, and which don't carry it at all:
[coverage.md](../coverage.md).
