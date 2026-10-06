"""
Walking the routed DRF views for which carry a rule, which are exempt and why, and which say nothing -
over this module's own urlconf (`urlpatterns` below), which no project setting has to point at.
"""

import sys

import pytest
from django.http import HttpResponse
from django.urls import include, path
from django.views import View
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from isik.django.apps.idempotency.coverage import idempotency_coverage
from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.drf.coverage import (
    Coverage,
    CoverageStatus,
    RoutedAction,
    ViewKind,
    request_policy_coverage,
    routed_actions,
    routed_views,
)
from isik.django.drf.viewsets import RequestPoliciesMixin, RequestPolicy


URLCONF = "tests.django.drf.test_coverage"


class SetUp(RequestPolicy):
    def allows(self, request, view):
        return True


class Unused(RequestPolicy):
    def allows(self, request, view):
        return True


class Gated(RequestPoliciesMixin, IdempotencyMixin, ViewSet):
    request_policies = [SetUp]
    set_up_exempt_actions = {"retrieve": "reading one changes nothing the gate protects"}
    idempotency_exempt_actions = {"preview": "validates without writing anything"}

    def list(self, request):
        return Response()

    def create(self, request):
        return Response()

    def retrieve(self, request, pk=None):
        return Response()

    def preview(self, request):
        return Response()


class Ungated(ViewSet):
    def create(self, request):
        return Response()


class PlainView(APIView):
    def get(self, request):
        return Response()

    def post(self, request):
        return Response()


class GatedPlainView(RequestPoliciesMixin, IdempotencyMixin, APIView):
    request_policies = [SetUp]
    idempotent_methods = ("PUT",)

    def post(self, request):
        return Response()


def not_drf(request):
    return HttpResponse()


class DjangoView(View):
    def get(self, request):
        return HttpResponse()

    def post(self, request):
        return HttpResponse()


router = SimpleRouter()
router.register("gated", Gated, basename="gated")
router.register("ungated", Ungated, basename="ungated")

urlpatterns = [
    *router.urls,
    # Before a DRF view, so one that isn't DRF's is passed over rather than ending the walk.
    path("not-drf-first/", not_drf),
    path("gated/preview/", Gated.as_view({"post": "preview"})),
    path("api/", include([path("plain/", PlainView.as_view()), path("gated-plain/", GatedPlainView.as_view())])),
    path("not-drf/", not_drf),
    path("site/", include([path("account/", include([path("email/", DjangoView.as_view())]))])),
]


def table(report):
    return [
        (entry.routed.route, entry.routed.method, entry.routed.action, entry.status, entry.reason) for entry in report
    ]


def test_every_method_of_every_drf_view_is_walked_in_route_order():
    assert [(a.route, a.view, a.method, a.action) for a in routed_actions(URLCONF)] == [
        ("^gated/$", Gated, "GET", "list"),
        ("^gated/$", Gated, "POST", "create"),
        ("^gated/(?P<pk>[^/.]+)/$", Gated, "GET", "retrieve"),
        ("^ungated/$", Ungated, "POST", "create"),
        ("gated/preview/", Gated, "POST", "preview"),
        ("api/plain/", PlainView, "GET", None),
        ("api/plain/", PlainView, "POST", None),
        ("api/gated-plain/", GatedPlainView, "POST", None),
    ]


def test_the_projects_own_urlconf_is_walked_by_default(settings):
    settings.ROOT_URLCONF = URLCONF

    assert routed_actions() == routed_actions(URLCONF)


def test_request_policy_coverage_names_each_actions_standing():
    assert table(request_policy_coverage(SetUp, URLCONF)) == [
        ("^gated/$", "GET", "list", "covered", None),
        ("^gated/$", "POST", "create", "covered", None),
        ("^gated/(?P<pk>[^/.]+)/$", "GET", "retrieve", "exempt", "reading one changes nothing the gate protects"),
        ("^ungated/$", "POST", "create", "uncovered", None),
        ("gated/preview/", "POST", "preview", "covered", None),
        ("api/plain/", "GET", None, "uncovered", None),
        ("api/plain/", "POST", None, "uncovered", None),
        ("api/gated-plain/", "POST", None, "covered", None),
    ]


def test_a_view_with_policies_but_not_this_one_is_uncovered():
    assert {entry.status for entry in request_policy_coverage(Unused, URLCONF)} == {CoverageStatus.UNCOVERED}


def test_idempotency_coverage_looks_at_the_methods_a_key_is_for():
    assert table(idempotency_coverage(URLCONF)) == [
        ("^gated/$", "POST", "create", "covered", None),
        ("^ungated/$", "POST", "create", "uncovered", None),
        ("gated/preview/", "POST", "preview", "exempt", "validates without writing anything"),
        ("api/plain/", "POST", None, "uncovered", None),
        # The mixin is there, but this view honors keys on PUT alone.
        ("api/gated-plain/", "POST", None, "uncovered", None),
    ]


def test_idempotency_coverage_can_ask_about_other_methods():
    assert table(idempotency_coverage(URLCONF, methods=("GET",))) == [
        ("^gated/$", "GET", "list", "uncovered", None),
        ("^gated/(?P<pk>[^/.]+)/$", "GET", "retrieve", "uncovered", None),
        ("api/plain/", "GET", None, "uncovered", None),
    ]


def test_an_entry_says_whether_it_is_uncovered():
    routed = RoutedAction(route="x/", view=Ungated, method="POST", action="create")

    assert Coverage(routed, CoverageStatus.UNCOVERED).is_uncovered is True
    assert Coverage(routed, CoverageStatus.EXEMPT, "why").is_uncovered is False
    assert Coverage(routed, CoverageStatus.COVERED).is_uncovered is False
    assert Coverage(routed, CoverageStatus.COVERED).reason is None


def test_every_view_is_walked_with_what_answers_it():
    assert [(a.route, a.view, a.method, a.action, a.kind) for a in routed_views(URLCONF)] == [
        ("^gated/$", Gated, "GET", "list", ViewKind.DRF),
        ("^gated/$", Gated, "POST", "create", ViewKind.DRF),
        ("^gated/(?P<pk>[^/.]+)/$", Gated, "GET", "retrieve", ViewKind.DRF),
        ("^ungated/$", Ungated, "POST", "create", ViewKind.DRF),
        ("not-drf-first/", not_drf, None, None, ViewKind.FUNCTION),
        ("gated/preview/", Gated, "POST", "preview", ViewKind.DRF),
        ("api/plain/", PlainView, "GET", None, ViewKind.DRF),
        ("api/plain/", PlainView, "POST", None, ViewKind.DRF),
        ("api/gated-plain/", GatedPlainView, "POST", None, ViewKind.DRF),
        ("not-drf/", not_drf, None, None, ViewKind.FUNCTION),
        ("site/account/email/", DjangoView, "GET", None, ViewKind.CLASS),
        ("site/account/email/", DjangoView, "POST", None, ViewKind.CLASS),
    ]


def test_routed_actions_are_the_drf_part_of_routed_views():
    assert routed_actions(URLCONF) == [a for a in routed_views(URLCONF) if a.kind is ViewKind.DRF]


def test_plain_views_join_the_report_as_the_project_judges_them():
    def plain_views(routed):
        if routed.view is DjangoView:
            return routed.method == "POST"
        if routed.route == "not-drf/":
            return "a health check, answering anyone"
        return False

    plain = [entry for entry in table(request_policy_coverage(SetUp, URLCONF, plain_views=plain_views))]
    assert [entry for entry in plain if entry[0] in ("not-drf-first/", "not-drf/", "site/account/email/")] == [
        ("not-drf-first/", None, None, "uncovered", None),
        ("not-drf/", None, None, "exempt", "a health check, answering anyone"),
        ("site/account/email/", "GET", None, "uncovered", None),
        ("site/account/email/", "POST", None, "covered", None),
    ]


def test_without_plain_views_only_drf_views_are_reported():
    routes = {entry.routed.route for entry in request_policy_coverage(SetUp, URLCONF)}
    assert routes.isdisjoint({"not-drf-first/", "not-drf/", "site/account/email/"})


def test_plain_views_must_answer_true_false_or_a_reason():
    with pytest.raises(
        TypeError, match=r"^plain_views\(\) answers True, False or a reason, not ' ' for not-drf-first/\.$"
    ):
        request_policy_coverage(SetUp, URLCONF, plain_views=lambda routed: " ")


def test_each_entry_names_the_urlconf_routing_it():
    assert {a.urlconf for a in routed_views(URLCONF)} == {URLCONF}
    assert {a.urlconf for a in routed_views(sys.modules[__name__])} == {URLCONF}


def test_by_default_every_django_hosts_urlconf_is_walked(settings):
    settings.ROOT_URLCONF = URLCONF
    settings.ROOT_HOSTCONF = "tests.django.apps.common.hosts"
    from tests.django.apps.common.hosted_urls import HostedGated

    walked = routed_views()

    assert walked[: len(routed_views(URLCONF))] == routed_views(URLCONF)
    assert [(a.route, a.view, a.method, a.kind, a.urlconf) for a in walked[len(routed_views(URLCONF)) :]] == [
        ("^hosted/$", HostedGated, "GET", ViewKind.DRF, "tests.django.apps.common.hosted_urls"),
    ]
    assert table(request_policy_coverage(SetUp))[-1] == (
        "^hosted/$",
        "GET",
        "list",
        "exempt",
        "a host's health check reads the list",
    )


def test_several_urlconfs_are_walked_in_order(settings):
    hosted = "tests.django.apps.common.hosted_urls"

    assert routed_views([hosted, URLCONF]) == [*routed_views(hosted), *routed_views(URLCONF)]


def test_each_entry_carries_the_callable_the_urlconf_routes():
    walked = routed_views("tests.django.drf.marked_urls")

    assert [(a.route, a.kind, a.callback.marked_in_the_urlconf) for a in walked if a.kind is ViewKind.DRF] == [
        ("drf/", ViewKind.DRF, True),
        ("drf/", ViewKind.DRF, True),
    ]
    assert [(a.route, a.method, a.callback.marked_in_the_urlconf) for a in walked if a.kind is not ViewKind.DRF] == [
        ("class/", "GET", True),
        ("class/", "POST", True),
        ("function/", None, True),
    ]
    assert all(a.callback.__name__ in ("view", "not_drf") for a in walked)
