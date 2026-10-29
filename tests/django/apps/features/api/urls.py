from django.urls import path
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.views import APIView

from isik.django.apps.features.drf import FeatureViewSet, HasFeature, SettingViewSet


class WebhookGate(APIView):
    """Only with the webhooks feature - what HasFeature gates."""

    permission_classes = [HasFeature("webhooks")]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"ok": True})


router = SimpleRouter()
router.register("features", FeatureViewSet, basename="feature")
router.register("settings", SettingViewSet, basename="setting")

urlpatterns = [*router.urls, path("webhooks/", WebhookGate.as_view())]
