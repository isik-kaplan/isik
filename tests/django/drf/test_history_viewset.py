import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django_filters.rest_framework import CharFilter
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIRequestFactory

from isik.django.drf.pagination import PageNumberPagination
from isik.django.drf.viewsets.base import BaseModelViewSet
from isik.django.drf.viewsets.history import HistoryMixin, context_filter
from tests.testapp.models import Comment, Widget


pytestmark = pytest.mark.django_db


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class WidgetViewSet(HistoryMixin, BaseModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
    exempt_from_registry = True


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(username="root", email="root@example.com", password="x")


def call_history(viewset_cls, widget, **query_params):
    request = APIRequestFactory().get(f"/widgets/{widget.pk}/history/", query_params)
    return viewset_cls.as_view({"get": "history"})(request, pk=widget.pk)


def call_history_list(viewset_cls, user=None, **query_params):
    request = APIRequestFactory().get("/widgets/history/", query_params)
    if user is not None:
        request.user = user
    return viewset_cls.as_view({"get": "history_list"})(request)


class TestHistoryMixin:
    def test_returns_events_newest_first(self):
        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=2)
        widget.update(count=3)

        response = call_history(WidgetViewSet, widget)

        assert response.status_code == 200
        assert [event["action"] for event in response.data] == ["update", "update", "insert"]
        assert [event["count"] for event in response.data] == [3, 2, 1]

    def test_uses_history_serializer_class_for_the_history_action(self):
        view = WidgetViewSet()
        view.action = "history"
        assert view.get_serializer_class() is WidgetViewSet.history_serializer_class
        view.action = "retrieve"
        assert view.get_serializer_class() is WidgetSerializer

    def test_get_object_runs_so_permission_filtering_applies(self):
        widget = Widget.objects.create(name="bolt", count=1)

        class ScopedViewSet(WidgetViewSet):
            model = Widget
            endpoint = "scoped-widgets"
            exempt_from_registry = True

            def get_queryset(self):
                return self.model.objects.none()

        response = call_history(ScopedViewSet, widget)
        assert response.status_code == 404

    def test_raises_on_an_untracked_model(self):
        class CommentSerializer(serializers.ModelSerializer):
            class Meta:
                model = Comment
                fields = ["id", "body"]

        class CommentViewSet(HistoryMixin, BaseModelViewSet):
            model = Comment
            endpoint = "comments"
            serializer_class = CommentSerializer
            exempt_from_registry = True

        comment = Comment.objects.create(body="hi", widget=Widget.objects.create(name="bolt", count=1))
        with pytest.raises(ImproperlyConfigured, match="has no @track_events"):
            call_history(CommentViewSet, comment)

    def test_filters_by_action(self):
        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=2)

        response = call_history(WidgetViewSet, widget, action="insert")

        assert [event["action"] for event in response.data] == ["insert"]

    def test_filters_by_created_after_and_created_before(self):
        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=2)
        far_future = "2999-01-01T00:00:00Z"
        far_past = "2000-01-01T00:00:00Z"

        assert call_history(WidgetViewSet, widget, created_after=far_future).data == []
        assert call_history(WidgetViewSet, widget, created_before=far_past).data == []
        assert len(call_history(WidgetViewSet, widget, created_after=far_past).data) == 2

    def test_actor_filter_is_absent_by_default(self):
        assert "actor" not in WidgetViewSet.history_filterset_class.base_filters

    def test_actor_filter_is_present_when_history_middleware_is_installed(self):
        class MiddlewareWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "mw-widgets"
            exempt_from_registry = True

        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            assert "actor" in MiddlewareWidgetViewSet.history_filterset_class.base_filters

    def test_extra_history_filters_adds_without_dropping_the_built_ins(self):
        class OrgWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "org-widgets"
            exempt_from_registry = True
            extra_history_filters = {"org_id": context_filter("org_id")}

        base_filters = OrgWidgetViewSet.history_filterset_class.base_filters
        assert "org_id" in base_filters
        assert "action" in base_filters

    def test_extra_history_filters_overrides_a_built_in_of_the_same_name(self):
        class RenamedActionFilterViewSet(WidgetViewSet):
            model = Widget
            endpoint = "renamed-widgets"
            exempt_from_registry = True
            extra_history_filters = {"action": CharFilter(field_name="pgh_label", lookup_expr="icontains")}

        filterset_field = RenamedActionFilterViewSet.history_filterset_class.base_filters["action"]
        assert isinstance(filterset_field, CharFilter)

    def test_history_filterset_class_is_cached_per_class(self):
        assert WidgetViewSet.history_filterset_class is WidgetViewSet.history_filterset_class

    def test_history_serializer_class_is_cached_per_class(self):
        assert WidgetViewSet.history_serializer_class is WidgetViewSet.history_serializer_class

    def test_filters_combine(self):
        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=2)
        widget.update(count=3)

        response = call_history(WidgetViewSet, widget, action="update", created_after="2000-01-01T00:00:00Z")

        assert [event["count"] for event in response.data] == [3, 2]

    def test_default_history_filters_overridden_wholesale_replaces_the_built_ins(self):
        class MinimalFilterWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "minimal-widgets"
            exempt_from_registry = True

            @classmethod
            def default_history_filters(cls):
                return {"action": CharFilter(field_name="pgh_label")}

        base_filters = MinimalFilterWidgetViewSet.history_filterset_class.base_filters
        assert set(base_filters) == {"action"}

    def test_pagination_wraps_the_response_when_a_pagination_class_is_set(self):
        class PaginatedWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "paginated-widgets"
            exempt_from_registry = True
            pagination_class = PageNumberPagination

        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=2)

        request = APIRequestFactory().get(f"/widgets/{widget.pk}/history/", {"page_size": 1})
        response = PaginatedWidgetViewSet.as_view({"get": "history"})(request, pk=widget.pk)

        assert response.status_code == 200
        assert response.data.keys() == {"count", "page_size", "total_pages", "results"}
        assert response.data["count"] == 2
        assert len(response.data["results"]) == 1

    def test_router_generates_both_history_urls(self):
        router = SimpleRouter()
        router.register(WidgetViewSet.endpoint, WidgetViewSet, basename="widget")
        urls_by_name = {url.name: str(url.pattern) for url in router.urls}

        assert urls_by_name["widget-history"] == r"^widgets/(?P<pk>[^/.]+)/history/$"
        assert urls_by_name["widget-history-list"] == r"^widgets/history/$"


class TestHistoryList:
    def test_superuser_can_access(self, superuser):
        Widget.objects.create(name="bolt", count=1)
        response = call_history_list(WidgetViewSet, user=superuser)
        assert response.status_code == 200

    def test_regular_user_is_denied(self, alice):
        response = call_history_list(WidgetViewSet, user=alice)
        assert response.status_code == 403

    def test_anonymous_user_is_denied(self):
        response = call_history_list(WidgetViewSet)
        assert response.status_code == 403

    def test_returns_events_across_every_instance(self, superuser):
        first = Widget.objects.create(name="bolt", count=1)
        second = Widget.objects.create(name="nut", count=1)

        response = call_history_list(WidgetViewSet, user=superuser)

        object_ids = {event["id"] for event in response.data}
        assert object_ids == {str(first.pk), str(second.pk)}

    def test_filters_by_object_id(self, superuser):
        first = Widget.objects.create(name="bolt", count=1)
        Widget.objects.create(name="nut", count=1)

        response = call_history_list(WidgetViewSet, user=superuser, object_id=str(first.pk))

        assert [event["id"] for event in response.data] == [str(first.pk)]

    def test_object_id_combines_with_action(self, superuser):
        first = Widget.objects.create(name="bolt", count=1)
        first.update(count=2)
        Widget.objects.create(name="nut", count=1)

        response = call_history_list(WidgetViewSet, user=superuser, object_id=str(first.pk), action="update")

        assert [event["count"] for event in response.data] == [2]

    def test_reaches_history_for_a_deleted_object(self, superuser):
        widget = Widget.objects.create(name="bolt", count=1)
        widget_id = str(widget.pk)
        widget.delete()

        response = call_history_list(WidgetViewSet, user=superuser, object_id=widget_id)

        assert [event["action"] for event in response.data] == ["delete", "insert"]

    def test_uses_history_serializer_class(self):
        view = WidgetViewSet()
        view.action = "history_list"
        assert view.get_serializer_class() is WidgetViewSet.history_serializer_class

    def test_history_list_permission_classes_is_overridable(self, alice):
        class OpenHistoryWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "open-widgets"
            exempt_from_registry = True
            history_list_permission_classes = [BasePermission]

        Widget.objects.create(name="bolt", count=1)
        response = call_history_list(OpenHistoryWidgetViewSet, user=alice)
        assert response.status_code == 200
