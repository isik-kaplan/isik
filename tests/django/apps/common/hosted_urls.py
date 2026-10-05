"""Routed only by the api host in `hosts.py` - its exemptions are found only by walking that host's urlconf."""

from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import ViewSet

from isik.django.drf.viewsets import RequestPoliciesMixin, ViewSetRegistryMixin
from tests.django.drf.test_coverage import SetUp
from tests.testapp.models import Widget


class HostedGated(RequestPoliciesMixin, ViewSetRegistryMixin, ViewSet):
    model = Widget
    exempt_from_registry = "routed only by the api host, beside the main widget viewset"
    request_policies = [SetUp]
    set_up_exempt_actions = {"list": "a host's health check reads the list"}

    def list(self, request):
        return Response()


router = SimpleRouter()
router.register("hosted", HostedGated, basename="hosted")

urlpatterns = router.urls
