import pytest
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.drf.utils.current_user import CurrentUser, CurrentUserField
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    owner = CurrentUserField()

    class Meta:
        model = Widget
        fields = ["id", "name", "count", "owner"]


class WidgetCreateOnlyOwnerSerializer(serializers.ModelSerializer):
    owner = CurrentUserField(create_only=True)

    class Meta:
        model = Widget
        fields = ["id", "name", "count", "owner"]


@pytest.fixture
def make_request():
    def _make_request(user=None):
        request = Request(APIRequestFactory().get("/"))
        if user is not None:
            request.user = user
        return request

    return _make_request


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="password")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="password")


class TestCurrentUserField:
    def test_resolves_to_the_current_user_on_create(self, alice, make_request):
        request = make_request(alice)
        serializer = WidgetSerializer(data={"name": "bolt", "count": 1}, context={"request": request})
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] == alice

    def test_is_none_for_an_anonymous_request(self, make_request):
        request = make_request()
        serializer = WidgetSerializer(data={"name": "bolt", "count": 1}, context={"request": request})
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] is None

    def test_a_client_supplied_owner_is_ignored(self, alice, bob, make_request):
        request = make_request(alice)
        serializer = WidgetSerializer(data={"name": "bolt", "count": 1, "owner": bob.id}, context={"request": request})
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] == alice

    def test_create_only_resolves_to_the_current_user_when_there_is_no_instance_yet(self, alice, make_request):
        request = make_request(alice)
        serializer = WidgetCreateOnlyOwnerSerializer(data={"name": "bolt", "count": 1}, context={"request": request})
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] == alice

    def test_create_only_keeps_the_existing_owner_on_update(self, alice, bob, make_request):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request(bob)
        serializer = WidgetCreateOnlyOwnerSerializer(
            widget, data={"name": "renamed", "count": 1}, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] == alice

    def test_create_only_false_reassigns_ownership_to_the_requesting_user_on_update(self, alice, bob, make_request):
        # Intentional behavior: without create_only=True, the field re-resolves to whoever is
        # making the request on every save, including updates - it does not pin to the instance's
        # existing owner. create_only=True is what a caller uses to opt out of that reassignment.
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        request = make_request(bob)
        serializer = WidgetSerializer(widget, data={"name": "renamed", "count": 1}, context={"request": request})
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] == bob

    def test_is_none_when_context_has_no_request_key(self):
        serializer = WidgetSerializer(data={"name": "bolt", "count": 1}, context={})
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] is None

    def test_is_none_when_context_is_omitted_entirely(self):
        serializer = WidgetSerializer(data={"name": "bolt", "count": 1})
        serializer.is_valid(raise_exception=True)
        assert serializer.validated_data["owner"] is None


class TestCurrentUser:
    def test_repr_shows_create_only(self):
        assert repr(CurrentUser(create_only=True)) == "CurrentUser(create_only=True)"
        assert repr(CurrentUser()) == "CurrentUser(create_only=False)"
