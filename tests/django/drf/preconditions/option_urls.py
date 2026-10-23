"""Views with each option changed - kept out of urls.py, whose views the check holds to sensible options."""

from rest_framework.routers import SimpleRouter

from tests.django.drf.preconditions import views


router = SimpleRouter()
router.register("lenient", views.LenientWidgetViewSet, basename="lenient")
router.register("optional", views.OptionalWidgetViewSet, basename="optional")
router.register("named-only", views.NamedOnlyWidgetViewSet, basename="named-only")
router.register("optional-named-only", views.OptionalNamedOnlyWidgetViewSet, basename="optional-named-only")
router.register("gone-fails", views.GoneFailsWidgetViewSet, basename="gone-fails")
router.register("always-in-full", views.AlwaysInFullWidgetViewSet, basename="always-in-full")
router.register("gone-underfoot", views.GoneUnderfootWidgetViewSet, basename="gone-underfoot")

urlpatterns = router.urls
