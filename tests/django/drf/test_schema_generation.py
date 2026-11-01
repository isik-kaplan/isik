import pytest
from rest_framework import serializers

from isik.django.drf.viewsets import ViewSetRegistryExemption
from isik.django.drf.viewsets.base import BaseModelViewSet
from isik.django.drf.viewsets.schema_generation import none_during_schema_generation
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


def user_scoped_viewset():
    """Made per test, so the decorator runs while the test does."""

    class UserScopedWidgetViewSet(BaseModelViewSet):
        model = Widget
        endpoint = "widgets"
        serializer_class = WidgetSerializer
        exempt_from_registry = ViewSetRegistryExemption(reason="a test's own class, defined again on every run")

        @none_during_schema_generation
        def get_queryset(self):
            return super().get_queryset().filter(name="mine")

    return UserScopedWidgetViewSet


class TestNoneDuringSchemaGeneration:
    def test_returns_the_real_queryset_for_a_normal_request(self):
        Widget.objects.create(name="mine", count=1)
        Widget.objects.create(name="not-mine", count=2)
        view = user_scoped_viewset()()
        assert [w.name for w in view.get_queryset()] == ["mine"]

    def test_returns_an_empty_queryset_during_schema_generation(self):
        Widget.objects.create(name="mine", count=1)
        view = user_scoped_viewset()()
        view.swagger_fake_view = True
        assert list(view.get_queryset()) == []

    def test_the_wrapped_method_gets_the_view_and_every_argument(self):
        @none_during_schema_generation
        def method(view, *args, **kwargs):
            return view, args, kwargs

        view = user_scoped_viewset()()

        assert method(view, 1, key=2) == (view, (1,), {"key": 2})
        assert method.__name__ == "method"


class TestWhatItAnswersWith:
    @pytest.fixture
    def faking(self):
        view = user_scoped_viewset()()
        view.swagger_fake_view = True
        return view

    def test_a_value(self, faking):
        @none_during_schema_generation(answer=())
        def administered(view):
            raise AssertionError("not run while a schema is generated")

        assert administered(faking) == ()

    def test_one_made_for_the_view(self, faking):
        @none_during_schema_generation(answer=lambda view: (view, "answered"))
        def administered(view):
            raise AssertionError("not run while a schema is generated")

        assert administered(faking) == (faking, "answered")

    def test_none_is_an_answer_too(self, faking):
        @none_during_schema_generation(answer=None)
        def administered(view):
            raise AssertionError("not run while a schema is generated")

        assert administered(faking) is None

    def test_with_parentheses_and_no_answer_its_the_empty_queryset(self, faking):
        @none_during_schema_generation()
        def administered(view):
            raise AssertionError("not run while a schema is generated")

        answered = administered(faking)
        assert answered.model is Widget and list(answered) == []

    def test_a_normal_request_runs_the_method(self):
        @none_during_schema_generation(answer=())
        def administered(view):
            return ["ran"]

        assert administered(user_scoped_viewset()()) == ["ran"]
        assert administered.__name__ == "administered"
