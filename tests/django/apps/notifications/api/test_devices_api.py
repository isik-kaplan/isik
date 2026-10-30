"""Devices through the API: an app registers its token with no key or version, sees its own, and forgets one."""

import pytest

from isik.django.apps.notifications.audience.helpers import register_device
from isik.django.apps.notifications.audience.models import Device
from isik.django.apps.notifications.choices import Platform
from tests.django.apps.notifications.api.test_api import client_for


pytestmark = pytest.mark.django_db


class TestDevices:
    def test_registering_one(self, alice):
        answer = client_for(alice).post(
            "/devices/", {"platform": "apns", "token": "abc", "environment": "sandbox"}, format="json"
        )

        assert answer.status_code == 201
        device = Device.objects.get()
        assert answer.json() == {
            "id": str(device.pk),
            "platform": "apns",
            "token": "abc",
            "environment": "sandbox",
            "app_id": "",
            "is_active": True,
            "last_seen_at": answer.json()["last_seen_at"],
        }
        assert (device.user, device.last_seen_at is not None) == (alice, True)

    def test_registering_one_registered_before_makes_it_the_callers(self, alice, bob):
        before = register_device(bob, Platform.FCM, "abc")
        Device.objects.filter(pk=before.pk).update(is_active=False)

        answer = client_for(alice).post("/devices/", {"platform": "fcm", "token": "abc"}, format="json")

        assert answer.status_code == 201
        assert answer.json()["id"] == str(before.pk)
        assert list(Device.objects.values_list("user", "is_active")) == [(alice.pk, True)]

    def test_what_it_refuses(self, alice):
        answer = client_for(alice).post("/devices/", {"platform": "pager", "token": "abc"}, format="json")

        assert answer.status_code == 400
        assert "platform" in answer.json()

    def test_listing_the_callers_own(self, alice, bob):
        mine = register_device(alice, Platform.APNS, "mine")
        register_device(bob, Platform.APNS, "theirs")

        assert [device["id"] for device in client_for(alice).get("/devices/").json()] == [str(mine.pk)]

    def test_forgetting_one(self, alice, bob):
        mine, theirs = register_device(alice, Platform.APNS, "mine"), register_device(bob, Platform.APNS, "theirs")
        client = client_for(alice)

        assert client.delete(f"/devices/{mine.pk}/").status_code == 204
        assert client.delete(f"/devices/{theirs.pk}/").status_code == 404
        assert list(Device.objects.values_list("token", flat=True)) == ["theirs"]

    def test_only_for_someone_signed_in(self):
        assert client_for().get("/devices/").status_code in (401, 403)
