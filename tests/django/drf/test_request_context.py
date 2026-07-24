import pytest
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from isik.django.drf.serializers.request_context import RequestContextMixin


pytestmark = pytest.mark.django_db


class DummySerializer(RequestContextMixin, serializers.Serializer):
    pass


@pytest.fixture
def make_request():
    def _make_request(user=None):
        request = Request(APIRequestFactory().get("/"))
        if user is not None:
            request.user = user
        return request

    return _make_request


class TestRequestContextMixin:
    def test_current_request_returns_the_request_from_context(self, make_request):
        request = make_request()
        serializer = DummySerializer(context={"request": request})
        assert serializer.current_request() is request

    def test_current_request_is_none_without_context(self):
        serializer = DummySerializer()
        assert serializer.current_request() is None

    def test_current_user_returns_the_authenticated_user(self, make_request, django_user_model):
        user = django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")
        serializer = DummySerializer(context={"request": make_request(user)})
        assert serializer.current_user() == user

    def test_current_user_is_none_for_an_anonymous_request(self, make_request):
        serializer = DummySerializer(context={"request": make_request()})
        assert serializer.current_user() is None

    def test_current_user_is_none_without_a_request_in_context(self):
        serializer = DummySerializer()
        assert serializer.current_user() is None
