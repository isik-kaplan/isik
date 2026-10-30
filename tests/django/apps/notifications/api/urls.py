from rest_framework.routers import SimpleRouter

from isik.django.apps.notifications.audience.swapping import get_endpoint_model
from isik.django.apps.notifications.drf import (
    DeliveryViewSet,
    DeviceViewSet,
    EndpointViewSet,
    InboxViewSet,
    NoticeKindViewSet,
    SubscriptionViewSet,
)


class EveryEndpointViewSet(EndpointViewSet):
    """The test project's endpoints - every one, to every caller."""

    def get_queryset(self):
        return get_endpoint_model()._default_manager.order_by("pk")


class Owned:
    # The test project's endpoints have no owner of their own; a URL naming the caller stands in for one.
    owner_field = "url"

    def owner(self, request):
        return f"https://{request.user.username}.example/hook"


class OwnedEndpointViewSet(Owned, EndpointViewSet):
    pass


class OwnedDeliveryViewSet(Owned, DeliveryViewSet):
    pass


router = SimpleRouter()
router.register("inbox", InboxViewSet, basename="inbox")
router.register("subscriptions", SubscriptionViewSet, basename="subscription")
router.register("kinds", NoticeKindViewSet, basename="kind")
router.register("devices", DeviceViewSet, basename="device")
router.register("endpoints", EveryEndpointViewSet, basename="endpoint")
router.register("owned-endpoints", OwnedEndpointViewSet, basename="owned-endpoint")
router.register("deliveries", OwnedDeliveryViewSet, basename="delivery")
# Served for their refusal: they don't say whose endpoints a caller sees.
router.register("unscoped-endpoints", EndpointViewSet, basename="unscoped-endpoint")
router.register("unscoped-deliveries", DeliveryViewSet, basename="unscoped-delivery")

urlpatterns = router.urls
