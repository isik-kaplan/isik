from rest_framework.routers import SimpleRouter

from tests.django.drf.preconditions import problem_views as views


router = SimpleRouter()
router.register("untokened", views.UntokenedViewSet, basename="untokened")
router.register("also-untokened", views.AlsoUntokenedViewSet, basename="also-untokened")
router.register("modelless", views.ModellessViewSet, basename="modelless")
router.register("stamp-writing", views.StampWritingViewSet, basename="stamp-writing")
router.register("version-writing", views.VersionWritingViewSet, basename="version-writing")
router.register("serializerless", views.SerializerlessViewSet, basename="serializerless")
router.register("plain", views.PlainViewSet, basename="plain")
router.register("no-deletes", views.NoDeletesViewSet, basename="no-deletes")

urlpatterns = router.urls
