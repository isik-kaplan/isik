import pghistory
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
from tests.testapp.models import Comment, ContextTrackedWidget, EmailUser, Widget


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


@pytest.fixture(autouse=True)
def _reset_widget_viewset_history_caches():
    # WidgetViewSet is module-level, so history_filterset_class/history_serializer_class's
    # classproperty caching (build once, reuse for the class's whole lifetime - correct in
    # production) would otherwise mean default_history_filters()/generic_history_serializer()
    # only ever actually runs once per interpreter, on whichever test happens to touch it first -
    # invisible under a normal test run, but it means a tool that reruns pytest in the same
    # process never re-exercises either function after that first call. Reset before every test.
    for attr in ("_history_filterset_class", "_history_serializer_class"):
        if attr in WidgetViewSet.__dict__:
            delattr(WidgetViewSet, attr)


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

    def test_action_choices_are_exactly_insert_update_delete(self):
        action_filter = WidgetViewSet.history_filterset_class.base_filters["action"]
        assert action_filter.extra["choices"] == [("insert", "insert"), ("update", "update"), ("delete", "delete")]

    def test_filters_by_created_after_and_created_before(self):
        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=2)
        far_future = "2999-01-01T00:00:00Z"
        far_past = "2000-01-01T00:00:00Z"

        assert call_history(WidgetViewSet, widget, created_after=far_future).data == []
        assert call_history(WidgetViewSet, widget, created_before=far_past).data == []
        assert len(call_history(WidgetViewSet, widget, created_after=far_past).data) == 2
        # created_before has to be an inclusive range (lookup_expr="lte"), not an exact match -
        # far_future never equals a real event timestamp, so an exact match would wrongly find 0.
        assert len(call_history(WidgetViewSet, widget, created_before=far_future).data) == 2

    def test_actor_filter_is_absent_by_default(self):
        assert "actor" not in WidgetViewSet.history_filterset_class.base_filters

    def test_actor_filter_is_present_when_history_middleware_is_installed(self):
        class MiddlewareWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "mw-widgets"
            exempt_from_registry = True

        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            assert "actor" in MiddlewareWidgetViewSet.history_filterset_class.base_filters

    def test_actor_id_reflects_the_pghistory_context_user_and_is_filterable(self):
        class ActorWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "actor-widgets"
            exempt_from_registry = True

        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            widget = Widget.objects.create(name="bolt", count=1)
            with pghistory.context(user=7):
                widget.update(count=2)

            response = call_history(ActorWidgetViewSet, widget)
            actor_by_action = {event["action"]: event["actor_id"] for event in response.data}
            assert actor_by_action == {"insert": None, "update": "7"}

            filtered = call_history(ActorWidgetViewSet, widget, actor=7)
            assert [event["action"] for event in filtered.data] == ["update"]

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

    def test_history_works_with_a_custom_lookup_field(self):
        # history()'s signature used to hardcode pk=None, so a viewset with a custom lookup_field
        # raised TypeError before reaching a line of it - a 500, not a 4xx.
        class SlugWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "slug-widgets"
            exempt_from_registry = True
            lookup_field = "name"

        widget = Widget.objects.create(name="bolt", count=1)

        request = APIRequestFactory().get(f"/slug-widgets/{widget.name}/history/")
        response = SlugWidgetViewSet.as_view({"get": "history"})(request, name=widget.name)

        assert response.status_code == 200
        assert response.data[0]["action"] == "insert"

    def test_invalid_filter_value_raises_instead_of_silently_filtering_nothing(self):
        widget = Widget.objects.create(name="bolt", count=1)

        response = call_history(WidgetViewSet, widget, created_after="not-a-date")

        assert response.status_code == 400
        assert "created_after" in response.data

    def test_history_withhold_removes_the_field_from_both_endpoints(self):
        class WithholdCountViewSet(WidgetViewSet):
            model = Widget
            endpoint = "withhold-widgets"
            exempt_from_registry = True
            history_withhold = ["count"]

        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=5)

        response = call_history(WithholdCountViewSet, widget)

        assert "count" not in response.data[0]
        # Newest first - index 0 is the update event, whose diff still names "count".
        assert response.data[0]["changes"]["count"] == [None, None]


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

    def test_context_field_actor_id_wins_over_the_pgh_context_json_annotation(self):
        # ContextTrackedWidget's "actor" ContextField already puts a real, typed actor_id column
        # on its event model - _history_base_queryset() must not also annotate one from JSON on
        # top of it (see isik/django/drf/viewsets/history.py).
        class ContextTrackedWidgetSerializer(serializers.ModelSerializer):
            class Meta:
                model = ContextTrackedWidget
                fields = ["id", "name"]

        class ContextTrackedWidgetViewSet(HistoryMixin, BaseModelViewSet):
            model = ContextTrackedWidget
            endpoint = "context-widgets"
            serializer_class = ContextTrackedWidgetSerializer
            exempt_from_registry = True

        alice = EmailUser.objects.create(username="alice", email="alice@example.com")
        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            with pghistory.context(user=alice.pk):
                widget = ContextTrackedWidget.objects.create(name="bolt")

            response = call_history(ContextTrackedWidgetViewSet, widget)

        assert response.data[0]["actor_id"] == alice.pk

    def test_history_base_queryset_omits_the_actor_id_annotation_when_a_context_field_covers_it(self):
        class ContextTrackedWidgetSerializer(serializers.ModelSerializer):
            class Meta:
                model = ContextTrackedWidget
                fields = ["id", "name"]

        class ContextTrackedWidgetViewSet(HistoryMixin, BaseModelViewSet):
            model = ContextTrackedWidget
            endpoint = "annotation-ctx-widgets"
            serializer_class = ContextTrackedWidgetSerializer
            exempt_from_registry = True

        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            queryset = ContextTrackedWidgetViewSet()._history_base_queryset()

        assert "actor_id" not in queryset.query.annotations

    def test_history_base_queryset_still_annotates_actor_id_when_nothing_covers_it(self):
        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            queryset = WidgetViewSet()._history_base_queryset()

        assert "actor_id" in queryset.query.annotations

    def test_history_list_permission_classes_is_overridable(self, alice):
        class OpenHistoryWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "open-widgets"
            exempt_from_registry = True
            history_list_permission_classes = [BasePermission]

        Widget.objects.create(name="bolt", count=1)
        response = call_history_list(OpenHistoryWidgetViewSet, user=alice)
        assert response.status_code == 200

    def test_history_list_is_unscoped_by_default_even_with_a_narrowing_get_queryset(self, superuser):
        class NarrowWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "narrow-widgets"
            exempt_from_registry = True

            def get_queryset(self):
                return self.model.objects.none()

        first = Widget.objects.create(name="bolt", count=1)
        second = Widget.objects.create(name="nut", count=1)

        response = call_history_list(NarrowWidgetViewSet, user=superuser)

        object_ids = {event["id"] for event in response.data}
        assert object_ids == {str(first.pk), str(second.pk)}

    def test_history_list_scoped_to_queryset_restricts_to_get_queryset(self, superuser):
        class ScopedListWidgetViewSet(WidgetViewSet):
            model = Widget
            endpoint = "scoped-list-widgets"
            exempt_from_registry = True
            history_list_scoped_to_queryset = True

            def get_queryset(self):
                return self.model.objects.filter(name="bolt")

        visible = Widget.objects.create(name="bolt", count=1)
        Widget.objects.create(name="nut", count=1)

        response = call_history_list(ScopedListWidgetViewSet, user=superuser)

        object_ids = {event["id"] for event in response.data}
        assert object_ids == {str(visible.pk)}
