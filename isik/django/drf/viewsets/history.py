"""HistoryMixin + context_filter() - see each one's own docstring."""

from django.db.models.fields.json import KeyTransform
from django.utils.functional import classproperty
from django_filters.rest_framework import CharFilter, ChoiceFilter, DateTimeFilter, FilterSet, NumberFilter
from pghistory.models import Events
from rest_framework.decorators import action
from rest_framework.response import Response

from isik.django.apps.common.db.history import event_model_for, history_middleware_installed
from isik.django.drf.permissions import IsSuperUser
from isik.django.drf.serializers.history import generic_history_serializer


def context_filter(key, filter_cls=NumberFilter, **kwargs):
    """Filter over a key in pghistory's context JSON - e.g. `context_filter("org_id")`."""
    kwargs.setdefault("field_name", f"pgh_context__{key}")
    return filter_cls(**kwargs)


class HistoryMixin:
    """
    Adds two endpoints to a `BaseModelViewSet` for a model tracked with `@track_events()`, both
    paginated (via the viewset's own `pagination_class`), newest first, using an auto-built
    serializer over the model's history (see `generic_history_serializer()`). No other
    configuration required - everything is resolved from `self.model` alone.

    `GET <endpoint>/{pk}/history/` - one object's history, governed by the viewset's own
    permissions same as any other detail action (`history()` calls `self.get_object()` first, so
    `get_queryset()`/permission filtering applies before any events are returned).

    `GET <endpoint>/history/` - history across every instance of the model, restricted to
    superusers for now (`IsSuperUser` - override `history_list_permission_classes` to change that).

        class WidgetViewSet(HistoryMixin, BaseModelViewSet):
            model = Widget
            endpoint = "widgets"
            serializer_class = WidgetSerializer

        GET /widgets/3/history/?action=update&created_after=2026-08-01T00:00:00Z
        GET /widgets/history/?object_id=3&action=update
        # [{"event_id": 12, "event_created_at": "...", "action": "update",
        #   "changes": {"count": [0, 5]}, "id": "...", "name": "New name", "count": 5}, ...]

    Filtering is built in on `action` (insert/update/delete), `created_after`/`created_before`
    (`pgh_created_at` range), `object_id` (which instance - redundant but harmless on the
    per-object endpoint, the main point of it on the cross-object one), and `actor` (pghistory's
    context user - only added if `pghistory.middleware.HistoryMiddleware`, or a subclass, is
    installed).

    Add your own filters via `extra_history_filters` - merged on top of the built-ins, so a
    matching key overrides one and a new key just adds one:

        class OrgWidgetViewSet(HistoryMixin, BaseModelViewSet):
            ...
            extra_history_filters = {"org_id": context_filter("org_id")}

    Override `default_history_filters()` instead to replace the built-in set entirely -
    `extra_history_filters` still layers on top of whatever that returns.
    """

    extra_history_filters = {}
    history_list_permission_classes = [IsSuperUser]

    @classmethod
    def default_history_filters(cls):
        filters = {
            "action": ChoiceFilter(
                field_name="pgh_label", choices=[("insert", "insert"), ("update", "update"), ("delete", "delete")]
            ),
            "created_after": DateTimeFilter(field_name="pgh_created_at", lookup_expr="gte"),
            "created_before": DateTimeFilter(field_name="pgh_created_at", lookup_expr="lte"),
            "object_id": CharFilter(field_name="pgh_obj_id"),
        }
        if history_middleware_installed():
            filters["actor"] = context_filter("user")
        return filters

    @classproperty
    def history_filterset_class(cls):
        # Cached on cls.__dict__ (not a base class's) - see FilterSetMixin.filterset_class, same
        # reasoning: rebuilt from scratch, not shared/inherited, if a subclass overrides
        # default_history_filters()/extra_history_filters.
        cached = cls.__dict__.get("_history_filterset_class")
        if cached is not None:
            return cached
        filters = {**cls.default_history_filters(), **cls.extra_history_filters}
        meta = type("Meta", (), {"model": Events, "fields": []})
        built = type("AutoHistoryFilterSet", (FilterSet,), {**filters, "Meta": meta})
        cls._history_filterset_class = built
        return built

    @classproperty
    def history_serializer_class(cls):
        cached = cls.__dict__.get("_history_serializer_class")
        if cached is not None:
            return cached
        built = generic_history_serializer(cls.model)
        cls._history_serializer_class = built
        return built

    def _history_base_queryset(self):
        queryset = Events.objects.across(event_model_for(self.model))
        if history_middleware_installed():
            # A SQL-level annotation, not a serializer-side `source="pgh_context.user"` - pgh_context
            # is null for any event created outside a request, and KeyTransform resolves that to a
            # clean SQL NULL instead of the Python-side None a dotted source would crash on.
            queryset = queryset.annotate(actor_id=KeyTransform("user", "pgh_context"))
        return queryset

    def get_history_queryset(self, obj):
        return self._history_base_queryset().tracks(obj).order_by("-pgh_id")

    def get_all_history_queryset(self):
        return self._history_base_queryset().order_by("-pgh_id")

    def _history_response(self, request, queryset):
        queryset = self.history_filterset_class(request.query_params, queryset=queryset).qs
        page = self.paginate_queryset(queryset)
        serializer = self.history_serializer_class(page if page is not None else queryset, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        return self._history_response(request, self.get_history_queryset(self.get_object()))

    @action(detail=False, methods=["get"], url_path="history", url_name="history-list")
    def history_list(self, request):
        return self._history_response(request, self.get_all_history_queryset())

    def get_permissions(self):
        if self.action == "history_list":
            return [permission() for permission in self.history_list_permission_classes]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action in ("history", "history_list"):
            return self.history_serializer_class
        return super().get_serializer_class()
