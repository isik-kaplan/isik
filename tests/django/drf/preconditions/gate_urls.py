"""Views whose read gates differ from their write gates - a 412's `current` must follow the read's."""

from rest_framework.routers import SimpleRouter

from tests.django.drf.preconditions import views


router = SimpleRouter()
router.register("write-not-read", views.WriteNotReadViewSet, basename="write-not-read")
router.register("narrower-reads", views.ReadsANarrowerQuerysetViewSet, basename="narrower-reads")
router.register("object-read-refused", views.ObjectReadRefusedViewSet, basename="object-read-refused")
router.register("object-refused", views.ObjectRefusedViewSet, basename="object-refused")
router.register("field-hiding", views.FieldHidingViewSet, basename="field-hiding")
router.register("own-rows", views.OwnRowsOnlyViewSet, basename="own-rows")

urlpatterns = router.urls
