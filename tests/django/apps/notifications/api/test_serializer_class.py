"""A subclass setting `serializer_class` gets it - the viewset builds its own only when none is set."""

import pytest
from rest_framework import serializers

from isik.django.apps.notifications import drf
from isik.django.apps.notifications.audience.swapping import (
    get_device_model,
    get_endpoint_model,
    get_inbox_item_model,
    get_subscription_model,
)
from isik.django.apps.notifications.swapping import get_delivery_attempt_model, get_delivery_model


BUILT = [
    (drf.InboxViewSet, lambda: drf.inbox_item_serializer(get_inbox_item_model())),
    (drf.SubscriptionViewSet, lambda: drf.subscription_serializer(get_subscription_model())),
    (drf.DeviceViewSet, lambda: drf.device_serializer(get_device_model())),
    (drf.EndpointViewSet, lambda: drf.endpoint_serializer(get_endpoint_model())),
    (drf.DeliveryViewSet, lambda: drf.delivery_serializer(get_delivery_model(), get_delivery_attempt_model())),
]


class Own(serializers.Serializer):
    pass


@pytest.mark.parametrize("viewset, built", BUILT, ids=[viewset.__name__ for viewset, _ in BUILT])
def test_the_subclass_s_own_serializer_class(viewset, built):
    subclass = type("Projects", (viewset,), {"serializer_class": Own})

    assert subclass().get_serializer_class() is Own
    assert viewset().get_serializer_class() is built()
