from django.urls import path
from rest_framework.routers import SimpleRouter

from tests.django.drf.preconditions import read_views as views


router = SimpleRouter()
router.register("me-widgets", views.MeViewSet, basename="me-widget")
router.register("unrouted", views.UnroutedReadViewSet, basename="unrouted")
router.register("silent", views.SilentReadViewSet, basename="silent")
router.register("unread", views.UnreadViewSet, basename="unread")
router.register("no-retrieve", views.NoRetrieveViewSet, basename="no-retrieve")
router.register("quiet", views.QuietRetrieveViewSet, basename="quiet")
router.register("unchecked-notes", views.UncheckedNoteViewSet, basename="unchecked-note")
router.register("modelless", views.ModellessReadViewSet, basename="modelless")
router.register("mixed", views.MixedReadViewSet, basename="mixed-read")
router.register("mixed", views.MixedWriteViewSet, basename="mixed-write")

urlpatterns = [
    *router.urls,
    # The same path, a read of one model and a write of another.
    path("pair/<uuid:pk>/", views.VersionedWidgetReadView.as_view()),
    path("pair/<uuid:pk>/", views.WidgetWriteView.as_view()),
    path("unchecked/<uuid:pk>/", views.UncheckedWidgetView.as_view()),
]
