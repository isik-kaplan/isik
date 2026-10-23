from django.urls import path
from rest_framework.routers import SimpleRouter

from tests.django.apps.common import check_views as views


router = SimpleRouter()
router.register("guarded", views.GuardedViewSet, basename="guarded")
router.register("unguarded", views.UnguardedViewSet, basename="unguarded")
router.register("read-only", views.ReadOnlyByMethodsViewSet, basename="read-only")

urlpatterns = [
    *router.urls,
    path("plain-create/", views.PlainCreateView.as_view()),
    path("ok/", views.ok),
    path("leaves-silently/", views.leaves_silently),
    path("leaves-saying-why/", views.leaves_saying_why),
    path("export/", views.ExportView.as_view()),
]
