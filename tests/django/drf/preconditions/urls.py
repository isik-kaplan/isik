from django.urls import path
from rest_framework.routers import SimpleRouter

from tests.django.drf.preconditions import views


router = SimpleRouter()
router.register("widgets", views.WidgetViewSet, basename="widget")
router.register("underfoot-widgets", views.UnderfootWidgetViewSet, basename="underfoot-widget")
router.register("versioned-widgets", views.VersionedWidgetViewSet, basename="versioned-widget")
router.register("idempotent-first-widgets", views.IdempotentFirstWidgetViewSet, basename="idempotent-first-widget")
router.register(
    "precondition-first-widgets", views.PreconditionFirstWidgetViewSet, basename="precondition-first-widget"
)

urlpatterns = [
    *router.urls,
    path("widget/", views.WidgetListView.as_view()),
    path("ping/", views.PingView.as_view()),
    path("widget/<uuid:pk>/", views.WidgetDetailView.as_view()),
]
