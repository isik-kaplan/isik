import types

import django_filters
import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import path
from django_filters import ChoiceFilter, FilterSet
from django_filters.rest_framework import CharFilter, DateTimeFilter, NumberFilter
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.views import APIView

from isik.django.drf.serializers.conditional_serializer import ConditionalSerializerMixin, relational_serializer
from isik.django.drf.spectacular import (
    AutoSchema,
    _conditional_serializer_parameters,
    _history_filter_parameters,
    _openapi_type_for_filter,
)
from isik.django.drf.viewsets.base import BaseModelViewSet
from isik.django.drf.viewsets.history import HistoryMixin
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


def generate_schema(*view_sets_and_basenames, urlpatterns=()):
    router = SimpleRouter()
    for view_set, basename in view_sets_and_basenames:
        router.register(view_set.endpoint, view_set, basename=basename)
    with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
        return SchemaGenerator(patterns=[*router.urls, *urlpatterns]).get_schema(request=None, public=True)


def stub_schema(*, action=..., tokenize_path=None):
    """A bare AutoSchema, bypassing drf-spectacular's own __init__ - enough state to exercise the
    history-action branches of get_operation_id()/_is_list_view() directly, without a real view,
    router, or request. `action=...` (the sentinel default) means "no .action attribute at all",
    matching a plain APIView - pass `action=None`/a string to simulate a real view instead."""
    schema = AutoSchema.__new__(AutoSchema)
    schema.view = types.SimpleNamespace() if action is ... else types.SimpleNamespace(action=action)
    if tokenize_path is not None:
        schema._tokenize_path = lambda: tokenize_path
    return schema


class OwnerSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username"]


class WidgetSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count", "owner"]
        relational_fields = {"owner": relational_serializer(OwnerSerializer)}


class WidgetViewSet(HistoryMixin, BaseModelViewSet):
    """A widget viewset."""

    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
    exempt_from_registry = True


@pytest.fixture(autouse=True)
def _reset_widget_viewset_history_caches():
    # WidgetViewSet is module-level - see the identical fixture in test_history_viewset.py for why
    # this has to run before every test rather than letting history_filterset_class's classproperty
    # caching silently reuse whatever the first test in this file happened to build it with (here,
    # whether HistoryMiddleware was installed at the time).
    for attr in ("_history_filterset_class", "_history_serializer_class"):
        if attr in WidgetViewSet.__dict__:
            delattr(WidgetViewSet, attr)


class TestHistoryMixinSchema:
    def test_both_history_endpoints_are_typed_as_returning_an_array(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        paths = schema["paths"]
        for endpoint_path in ("/widgets/{id}/history/", "/widgets/history/"):
            response_schema = paths[endpoint_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            assert response_schema["type"] == "array"

    def test_the_two_history_operation_ids_do_not_collide(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        paths = schema["paths"]
        object_op_id = paths["/widgets/{id}/history/"]["get"]["operationId"]
        list_op_id = paths["/widgets/history/"]["get"]["operationId"]
        assert object_op_id != list_op_id

    def test_built_in_filters_appear_as_query_parameters(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        params = schema["paths"]["/widgets/history/"]["get"]["parameters"]
        names = {param["name"] for param in params}
        assert {"action", "created_after", "created_before", "object_id"} <= names

    def test_the_action_filter_is_typed_as_a_string_enum(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        params = schema["paths"]["/widgets/history/"]["get"]["parameters"]
        action_param = next(param for param in params if param["name"] == "action")
        assert action_param["schema"]["type"] == "string"
        assert set(action_param["schema"]["enum"]) == {"insert", "update", "delete"}

    def test_the_created_after_filter_is_typed_as_a_datetime(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        params = schema["paths"]["/widgets/history/"]["get"]["parameters"]
        created_after_param = next(param for param in params if param["name"] == "created_after")
        assert created_after_param["schema"] == {"type": "string", "format": "date-time"}

    def test_an_integer_field_filter_is_typed_as_an_integer(self):
        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            schema = generate_schema((WidgetViewSet, "widget"))
        params = schema["paths"]["/widgets/history/"]["get"]["parameters"]
        actor_param = next(param for param in params if param["name"] == "actor")
        assert actor_param["schema"]["type"] == "integer"

    def test_a_plain_number_filter_is_typed_as_a_number(self):
        class NumberFilterWidgetViewSet(WidgetViewSet):
            endpoint = "number-filter-widgets"
            exempt_from_registry = True
            extra_history_filters = {"count": NumberFilter()}

        schema = generate_schema((NumberFilterWidgetViewSet, "number-filter-widget"))
        params = schema["paths"]["/number-filter-widgets/history/"]["get"]["parameters"]
        count_param = next(param for param in params if param["name"] == "count")
        assert count_param["schema"]["type"] == "number"

    def test_non_history_actions_are_unaffected(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        list_response_schema = schema["paths"]["/widgets/"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert list_response_schema["type"] == "array"
        assert schema["paths"]["/widgets/"]["get"]["operationId"] == "widgets_list"

    def test_history_operation_ids_are_exactly_this_shape(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        assert schema["paths"]["/widgets/{id}/history/"]["get"]["operationId"] == "widgets_history"
        assert schema["paths"]["/widgets/history/"]["get"]["operationId"] == "widgets_history_list"

    def test_a_plain_apiview_with_no_action_attribute_does_not_crash(self):
        # APIView (unlike a ViewSet) has no .action at all - exercises every getattr(self.view,
        # "action", None) fallback in AutoSchema without going through HistoryMixin.
        class PlainAPIView(APIView):
            def get(self, request):
                return Response({})

        schema = generate_schema(urlpatterns=[path("plain/", PlainAPIView.as_view())])
        assert "/plain/" in schema["paths"]


class TestAutoSchemaOperationIdUnit:
    def test_joins_multiple_tokens_with_an_underscore(self):
        schema = stub_schema(action="history_list", tokenize_path=["orgs", "widgets", "history"])
        assert schema.get_operation_id() == "orgs_widgets_history_list"

    def test_falls_back_to_root_when_no_tokens_remain(self):
        schema = stub_schema(action="history", tokenize_path=["history"])
        assert schema.get_operation_id() == "root_history"

    def test_strips_only_the_history_token_not_others(self):
        schema = stub_schema(action="history", tokenize_path=["widgets", "history", "extra"])
        assert schema.get_operation_id() == "widgets_extra_history"


class TestAutoSchemaIsListViewUnit:
    def test_true_for_the_history_action(self):
        assert stub_schema(action="history")._is_list_view() is True

    def test_true_for_the_history_list_action(self):
        assert stub_schema(action="history_list")._is_list_view() is True


class TestConditionalSerializerMixinSchema:
    def test_only_exclude_include_appear_as_query_parameters(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        params = schema["paths"]["/widgets/"]["get"]["parameters"]
        names = {param["name"] for param in params}
        assert {"only", "exclude", "include"} <= names

    def test_include_enum_comes_from_meta_relational_fields(self):
        schema = generate_schema((WidgetViewSet, "widget"))
        params = schema["paths"]["/widgets/"]["get"]["parameters"]
        include_param = next(param for param in params if param["name"] == "include")
        assert include_param["schema"]["items"]["enum"] == ["owner"]

    def test_a_serializer_without_the_mixin_gets_no_extra_parameters(self):
        class PlainWidgetSerializer(serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name", "count"]

        class PlainWidgetViewSet(BaseModelViewSet):
            """A plain widget viewset."""

            model = Widget
            endpoint = "plain-widgets"
            serializer_class = PlainWidgetSerializer
            exempt_from_registry = True

        schema = generate_schema((PlainWidgetViewSet, "plain-widget"))
        params = schema["paths"]["/plain-widgets/"]["get"]["parameters"]
        names = {param["name"] for param in params}
        assert not ({"only", "exclude", "include"} & names)


class TestOpenApiTypeForFilter:
    def test_a_choice_filter_is_a_string(self):
        assert _openapi_type_for_filter(ChoiceFilter(choices=[("a", "a")])) == OpenApiTypes.STR

    def test_a_datetime_filter_is_a_datetime(self):
        assert _openapi_type_for_filter(DateTimeFilter()) == OpenApiTypes.DATETIME

    def test_a_plain_number_filter_is_a_number(self):
        assert _openapi_type_for_filter(django_filters.NumberFilter()) == OpenApiTypes.NUMBER

    def test_an_integer_field_filter_is_an_integer(self):
        class IntegerNumberFilter(NumberFilter):
            field_class = forms.IntegerField

        assert _openapi_type_for_filter(IntegerNumberFilter()) == OpenApiTypes.INT

    def test_a_char_filter_falls_back_to_string(self):
        assert _openapi_type_for_filter(CharFilter()) == OpenApiTypes.STR


class TestHistoryFilterParametersUnit:
    def test_full_parameter_shape_for_every_attribute(self):
        class AsymmetricChoiceFilter(ChoiceFilter):
            pass

        class SampleFilterSet(FilterSet):
            status = AsymmetricChoiceFilter(choices=[("i", "Insert"), ("u", "Update")])
            name = CharFilter()

        parameters = _history_filter_parameters(SampleFilterSet)
        by_name = {parameter.name: parameter for parameter in parameters}

        assert by_name["status"].type == OpenApiTypes.STR
        assert by_name["status"].location == "query"
        assert by_name["status"].required is False
        assert by_name["status"].enum == ["i", "u"]

        assert by_name["name"].type == OpenApiTypes.STR
        assert by_name["name"].location == "query"
        assert by_name["name"].required is False
        assert by_name["name"].enum is None


class TestConditionalSerializerParametersUnit:
    def test_full_parameter_shape_for_every_attribute(self):
        parameters = _conditional_serializer_parameters(WidgetSerializer)
        by_name = {parameter.name: parameter for parameter in parameters}

        assert by_name["only"].type == OpenApiTypes.STR
        assert by_name["only"].location == "query"
        assert by_name["only"].many is True
        assert by_name["only"].required is False
        assert by_name["only"].enum is None
        assert (
            by_name["only"].description
            == "Narrow the response to just these fields (dotted paths reach into nested serializers)."
        )

        assert by_name["exclude"].type == OpenApiTypes.STR
        assert by_name["exclude"].location == "query"
        assert by_name["exclude"].many is True
        assert by_name["exclude"].required is False
        assert by_name["exclude"].enum is None
        exclude_description = "Drop these fields from the response - applied last, wins over only=/include=."
        assert by_name["exclude"].description == exclude_description

        assert by_name["include"].type == OpenApiTypes.STR
        assert by_name["include"].location == "query"
        assert by_name["include"].many is True
        assert by_name["include"].required is False
        assert by_name["include"].enum == ["owner"]
        assert by_name["include"].description == "Nest these normally-absent relational fields into the response."

    def test_include_enum_is_none_without_relational_fields(self):
        class NoRelationsSerializer(ConditionalSerializerMixin, serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name"]

        parameters = _conditional_serializer_parameters(NoRelationsSerializer)
        include_param = next(parameter for parameter in parameters if parameter.name == "include")
        assert include_param.enum is None

    def test_a_serializer_with_no_meta_class_at_all_does_not_crash(self):
        # A plain Serializer (unlike ModelSerializer) doesn't require a Meta at all.
        class NoMetaSerializer(ConditionalSerializerMixin, serializers.Serializer):
            name = serializers.CharField()

        parameters = _conditional_serializer_parameters(NoMetaSerializer)
        include_param = next(parameter for parameter in parameters if parameter.name == "include")
        assert include_param.enum is None
