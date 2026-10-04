"""
Walking the routed DRF views for which carry a rule, which are exempt and why, and which say nothing -
over this module's own urlconf (`urlpatterns` below), which no project setting has to point at.
"""

from django.http import HttpResponse
from django.urls import include, path
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from isik.django.apps.idempotency.coverage import idempotency_coverage
from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.drf.coverage import Coverage, CoverageStatus, RoutedAction, request_policy_coverage, routed_actions
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
