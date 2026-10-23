"""Views isik's system checks are run over, served through `check_urls.py`."""

from django.db import transaction
from django.http import HttpResponse
from django.views import View
from rest_framework import serializers
from rest_framework.generics import CreateAPIView

from isik.django.apps.common.atomic import not_atomic
from isik.django.drf.viewsets import BaseModelViewSet, ViewSetRegistryExemption
from tests.django.drf.support import UnguardedModelViewSet
from tests.testapp.models import VersionedWidget, Widget


EXEMPT = ViewSetRegistryExemption(reason="a view the checks are run over, beside the one registered for its model")


class VersionedWidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = VersionedWidget
        fields = ["id", "name"]


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name"]


class GuardedViewSet(BaseModelViewSet):
    model = VersionedWidget
    endpoint = "guarded"
    serializer_class = VersionedWidgetSerializer
    exempt_from_registry = EXEMPT


class UnguardedViewSet(UnguardedModelViewSet):
    model = Widget
    endpoint = "unguarded"
    serializer_class = WidgetSerializer
    exempt_from_registry = EXEMPT


class ReadOnlyByMethodsViewSet(UnguardedModelViewSet):
    """Its router maps every action; its http_method_names refuse the writes before they run."""

    http_method_names = ["get", "head", "options"]
    model = Widget
    endpoint = "read-only"
    serializer_class = WidgetSerializer
    exempt_from_registry = EXEMPT


class PlainCreateView(CreateAPIView):
    """Not a viewset: no action, and named by its class alone."""

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer


def ok(request):
    return HttpResponse("ok")


@transaction.non_atomic_requests
def leaves_silently(request):
    return HttpResponse("ok")


@not_atomic("streams a long export, which one transaction would hold open for minutes")
def leaves_saying_why(request):
    return HttpResponse("ok")


@not_atomic("streams a long export, which one transaction would hold open for minutes", using="default")
class ExportView(View):
    def get(self, request):
        return HttpResponse("ok")
