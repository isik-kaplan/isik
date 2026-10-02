from django.urls import path
from rest_framework.routers import SimpleRouter

from tests.django.apps.idempotency import views


router = SimpleRouter()
router.register("widgets", views.WidgetViewSet, basename="widget")
router.register("body-widgets", views.WidgetBodyViewSet, basename="body-widget")
router.register("nonce-blind-widgets", views.NonceBlindWidgetViewSet, basename="nonce-blind-widget")
router.register("optional-widgets", views.OptionalKeyWidgetViewSet, basename="optional-widget")

urlpatterns = [*router.urls, path("create-widget/", views.CreateWidgetView.as_view())]
