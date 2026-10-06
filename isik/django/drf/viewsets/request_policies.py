"""RequestPolicy + RequestPoliciesMixin - see each one's own docstring."""

from isik._internal.reasons import require_reasons
from isik._internal.translation import gettext_lazy
from isik.common.utils.strings import camel_to_snake


class RequestPolicy:
    """
    A statement about a request that a view refuses it without - "this organization is set up", "this
    account has its second factor" - as opposed to a permission over a model surface. Applied to any
    DRF view, viewset or not, through `RequestPoliciesMixin`:

        class OrganizationIsSetUp(RequestPolicy):
            message = _("This organization is still being set up.")
            code = "organization_not_set_up"
            exemptions_attribute = "setup_gate_exempt_actions"

            def allows(self, request, view):
                return request.tenant.is_set_up

    - `allows(request, view)` - whether the request may go on.
    - `message`/`code` - what the refusal says (a 403, or a 401 for an unauthenticated request, as DRF
      decides for any permission).
    - `refused(request, view, response)` - called with the refusal's response, to add what DRF can't
      render from a permission: a header telling a client which policy refused it, say.
    - `exemptions_attribute` - the view attribute naming the actions exempt from this policy, as
      `{action: reason}`. `<policy_name>_exempt_actions` unless set.
    """

    message = gettext_lazy("This request isn't allowed.")
    code = "request_policy"
    exemptions_attribute = None

    def allows(self, request, view):
        raise NotImplementedError

    def refused(self, request, view, response):
        """Adds to the refusal's `response` - nothing by default."""

    @classmethod
    def exemptions_attribute_name(cls):
        return cls.exemptions_attribute or f"{camel_to_snake(cls.__name__)}_exempt_actions"


class RequestPoliciesMixin:
    """
    Runs `request_policies` on every request, after the view's own permissions - so a policy holds
    for a view whatever else it is, and a `get_permissions()` override can't drop one by accident:

        class AccessTokenViewSet(RequestPoliciesMixin, viewsets.ViewSet):
            request_policies = [OrganizationIsSetUp, SecondFactorSatisfied]
            setup_gate_exempt_actions = {"me": "answers who is asking, which a blocked caller needs"}

    The first policy that doesn't allow the request refuses it, and its `refused()` hook then sees the
    response. An action exempt from a policy is named in that policy's exemptions attribute with the
    reason it's exempt - refused at class-definition time without one.

    `isik.django.drf.coverage.request_policy_coverage()` reports, over routed views, which carry a
    policy, which are exempt and why, and which don't carry it at all.
    """

    request_policies = ()

    # Per request: the policy that refused it, if one did.
    refused_by = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for policy in cls.request_policies:
            name = policy.exemptions_attribute_name()
            require_reasons(cls.__name__, name, getattr(cls, name, {}))

    @classmethod
    def request_policy_exemptions(cls, policy):
        """`{action: reason}` for the actions exempt from `policy` on this view."""
        return getattr(cls, policy.exemptions_attribute_name(), {})

    def check_permissions(self, request):
        super().check_permissions(request)
        action = getattr(self, "action", None)
        for policy_cls in self.request_policies:
            if action in self.request_policy_exemptions(policy_cls):
                continue
            policy = policy_cls()
            if not policy.allows(request, self):
                self.refused_by = policy
                self.permission_denied(request, message=policy.message, code=policy.code)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if self.refused_by is not None:
            self.refused_by.refused(request, self, response)
        return response
