"""
Request policies on any DRF view - a viewset over no model of ours, a plain APIView, a model viewset -
and BaseViewSet, the model-free base that carries them.
"""

import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ViewSet

from isik.django.apps.idempotency.by_reference.models import IdempotencyClaim
from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.drf.viewsets import (
    ActionSerializerClassMixin,
    BaseModelViewSet,
    BaseViewSet,
    GuardedFieldsMixin,
    RequestPoliciesMixin,
    RequestPolicy,
)
from tests.django.drf.test_base_viewset import WidgetSerializer
from tests.testapp.models import EmailUser, Widget


class Switch:
    """What the test policies below read - flipped per test."""

    set_up = True
    second_factor = True


class OrganizationIsSetUp(RequestPolicy):
    message = "This organization is still being set up."
    code = "organization_not_set_up"
    exemptions_attribute = "setup_gate_exempt_actions"

    def allows(self, request, view):
        return Switch.set_up


class SecondFactorSatisfied(RequestPolicy):
    message = "Set up a second factor first."
    code = "second_factor_required"

    def allows(self, request, view):
        return Switch.second_factor

    def refused(self, request, view, response):
        response["X-Second-Factor-Required"] = "1"


@pytest.fixture(autouse=True)
def switch(monkeypatch):
    monkeypatch.setattr(Switch, "set_up", True)
    monkeypatch.setattr(Switch, "second_factor", True)
    return Switch


@pytest.fixture
def alice(db):
    return EmailUser.objects.create(username="alice", email="alice@example.com")


class TokenViewSet(RequestPoliciesMixin, ViewSet):
    """Over no model of ours - what sat outside every gate when the gates lived on the model base."""

    request_policies = [OrganizationIsSetUp, SecondFactorSatisfied]
    setup_gate_exempt_actions = {"me": "answers who is asking, which a blocked caller needs to learn why"}

    def list(self, request):
        return Response(["token"])

    @action(detail=False)
    def me(self, request):
        return Response({"me": True})


class TokenView(RequestPoliciesMixin, APIView):
    request_policies = [OrganizationIsSetUp]

    def get(self, request):
        return Response("ok")


def call(view, user=None, method="get", **initkwargs):
    request = getattr(APIRequestFactory(), method)("/", format="json")
    if user is not None:
        force_authenticate(request, user=user)
    return view(request)


def list_tokens(user, **kwargs):
    return call(TokenViewSet.as_view({"get": "list"}), user, **kwargs)


class TestARequestPolicy:
    def test_a_request_it_allows_goes_on(self, alice):
        response = list_tokens(alice)

        assert (response.status_code, response.data) == (200, ["token"])

    def test_a_request_it_refuses_is_told_why(self, alice, switch):
        switch.set_up = False

        response = list_tokens(alice)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["detail"] == "This organization is still being set up."
        assert response.data["detail"].code == "organization_not_set_up"

    def test_an_unauthenticated_request_it_refuses_is_asked_to_authenticate(self, switch):
        switch.set_up = False

        response = list_tokens(None)

        assert response.data["detail"].code == "not_authenticated"

    def test_the_first_policy_refusing_is_the_one_heard(self, alice, switch):
        switch.set_up = switch.second_factor = False

        assert list_tokens(alice).data["detail"].code == "organization_not_set_up"

    def test_a_refusal_can_add_to_its_response(self, alice, switch):
        switch.second_factor = False

        response = list_tokens(alice)

        assert response.data["detail"].code == "second_factor_required"
        assert response["X-Second-Factor-Required"] == "1"

    def test_a_response_it_didnt_refuse_is_left_alone(self, alice):
        assert "X-Second-Factor-Required" not in list_tokens(alice)

    def test_a_refusal_by_another_policy_isnt_added_to(self, alice, switch):
        switch.set_up = False

        assert "X-Second-Factor-Required" not in list_tokens(alice)

    def test_an_exempt_action_skips_only_that_policy(self, alice, switch):
        switch.set_up = False
        me = TokenViewSet.as_view({"get": "me"})

        assert call(me, alice).status_code == 200

        switch.second_factor = False
        assert call(me, alice).data["detail"].code == "second_factor_required"

    def test_holds_on_a_plain_api_view(self, alice, switch):
        assert call(TokenView.as_view(), alice).status_code == 200

        switch.set_up = False
        assert call(TokenView.as_view(), alice).data["detail"].code == "organization_not_set_up"

    def test_runs_after_the_views_own_permissions(self, alice, switch):
        class Closed(BasePermission):
            message = "closed"

            def has_permission(self, request, view):
                return False

        class Guarded(TokenView):
            permission_classes = [Closed]

        switch.set_up = False

        assert call(Guarded.as_view(), alice).data["detail"] == "closed"

    def test_a_get_permissions_override_cant_drop_it(self, alice, switch):
        class Overridden(TokenView):
            def get_permissions(self):
                return []

        switch.set_up = False

        assert call(Overridden.as_view(), alice).status_code == status.HTTP_403_FORBIDDEN

    def test_is_refused_before_an_idempotency_key_is_claimed(self, alice, switch, monkeypatch):
        monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", True)

        class Claiming(IdempotencyMixin, TokenView):
            idempotency_claim_model = IdempotencyClaim

            def post(self, request):
                return Response(status=204)

        switch.set_up = False
        request = APIRequestFactory().post("/", headers={"Idempotency-Key": str(uuid.uuid4())})
        force_authenticate(request, user=alice)

        # The transaction ATOMIC_REQUESTS would open around the view - DRF rolls it back on the refusal.
        with transaction.atomic():
            response = Claiming.as_view()(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not IdempotencyClaim.objects.exists()


class TestWhatAPolicyIsAsked:
    def test_it_is_handed_the_request_and_the_view(self, alice):
        seen = []

        class Recording(RequestPolicy):
            def allows(self, request, view):
                seen.append(("allows", request, view))
                return False

            def refused(self, request, view, response):
                seen.append(("refused", request, view))

        class Recorded(RequestPoliciesMixin, APIView):
            request_policies = [Recording]

            def get(self, request):
                return Response()

        view = Recorded.as_view()
        request = APIRequestFactory().get("/")
        force_authenticate(request, user=alice)
        view(request)

        (_, asked_request, asked_view), (_, refused_request, refused_view) = seen
        assert [entry[0] for entry in seen] == ["allows", "refused"]
        assert asked_request is refused_request
        assert asked_request._request is request
        assert asked_view is refused_view
        assert isinstance(asked_view, Recorded)


class TestDeclaringOne:
    def test_its_exemptions_live_under_its_own_name_by_default(self):
        assert SecondFactorSatisfied.exemptions_attribute_name() == "second_factor_satisfied_exempt_actions"

    def test_its_exemptions_attribute_can_be_named(self):
        assert OrganizationIsSetUp.exemptions_attribute_name() == "setup_gate_exempt_actions"

    def test_a_view_lists_its_exemptions_per_policy(self):
        assert TokenViewSet.request_policy_exemptions(OrganizationIsSetUp) == TokenViewSet.setup_gate_exempt_actions
        assert TokenViewSet.request_policy_exemptions(SecondFactorSatisfied) == {}

    @pytest.mark.parametrize("exemptions", [{"me": ""}, {"me": True}, ("me",)])
    def test_an_exemption_needs_a_reason(self, exemptions):
        with pytest.raises(ImproperlyConfigured, match=r"^Unreasoned\.setup_gate_exempt_actions "):
            type(
                "Unreasoned",
                (RequestPoliciesMixin, ViewSet),
                {"request_policies": [OrganizationIsSetUp], "setup_gate_exempt_actions": exemptions},
            )

    def test_every_policy_has_its_exemptions_checked(self):
        with pytest.raises(ImproperlyConfigured, match=r"second_factor_satisfied_exempt_actions needs a reason"):
            type(
                "Unreasoned",
                (RequestPoliciesMixin, ViewSet),
                {
                    "request_policies": [OrganizationIsSetUp, SecondFactorSatisfied],
                    "second_factor_satisfied_exempt_actions": {"me": ""},
                },
            )

    def test_a_policy_says_something_by_default(self):
        assert (str(RequestPolicy.message), RequestPolicy.code) == ("This request isn't allowed.", "request_policy")

    def test_a_policy_adds_nothing_to_its_refusal_by_default(self):
        response = Response()

        assert RequestPolicy().refused(None, None, response) is None
        assert dict(response.items()) == dict(Response().items())

    def test_a_subclass_keeps_its_parents_init_subclass(self):
        seen = []

        class Recording:
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
                seen.append(cls.__name__)

        type("Recorded", (RequestPoliciesMixin, Recording), {})

        assert seen == ["Recorded"]

    def test_the_hooks_hand_their_exact_arguments_on(self):
        received = []

        class RecordingBase:
            def check_permissions(self, request):
                received.append(("check_permissions", request))

            def finalize_response(self, request, response, *args, **kwargs):
                received.append(("finalize_response", request, response, args, kwargs))
                return response

        class Recorded(RequestPoliciesMixin, RecordingBase):
            pass

        request, response = object(), object()
        view = Recorded()
        view.check_permissions(request)
        assert view.finalize_response(request, response, "extra", pk="1") is response

        assert received == [
            ("check_permissions", request),
            ("finalize_response", request, response, ("extra",), {"pk": "1"}),
        ]


class TestBaseViewSet:
    def test_is_a_model_free_viewset_with_the_request_level_mixins(self):
        assert BaseViewSet.__bases__ == (
            RequestPoliciesMixin,
            ActionSerializerClassMixin,
            GuardedFieldsMixin,
            GenericViewSet,
        )
        assert not hasattr(BaseViewSet, "model")

    def test_carries_request_policies(self, alice, switch):
        class Tokens(BaseViewSet):
            request_policies = [OrganizationIsSetUp]

            def list(self, request):
                return Response([])

        switch.set_up = False

        assert call(Tokens.as_view({"get": "list"}), alice).data["detail"].code == "organization_not_set_up"

    def test_the_model_viewset_is_built_on_it(self):
        assert issubclass(BaseModelViewSet, BaseViewSet)

    def test_a_model_viewset_carries_request_policies_too(self, alice, switch):
        class Widgets(BaseModelViewSet):
            model = Widget
            endpoint = "widgets"
            serializer_class = WidgetSerializer
            exempt_from_registry = "a test's own class, defined again on every run"
            request_policies = [OrganizationIsSetUp]

        switch.set_up = False

        assert call(Widgets.as_view({"get": "list"}), alice).data["detail"].code == "organization_not_set_up"
