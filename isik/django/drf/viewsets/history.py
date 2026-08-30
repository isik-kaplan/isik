"""HistoryMixin + context_filter()/context_field_filter() - see each one's own docstring."""

from django import forms
from django.db.models.fields.json import KeyTransform
from django.utils.functional import classproperty
from django_filters.constants import EMPTY_VALUES
from django_filters.rest_framework import CharFilter, ChoiceFilter, DateTimeFilter, FilterSet, NumberFilter
from pghistory.models import Events
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from isik.django.apps.common.db.history import event_model_for, history_middleware_installed
from isik.django.drf.permissions import IsSuperUser
from isik.django.drf.serializers.history import generic_history_serializer


class _JSONContextNumberFilter(NumberFilter):
    # NumberFilter's default field_class (forms.DecimalField) cleans values into Decimal, which
    # psycopg's JSON parameter adapter can't serialize when comparing against a pghistory context
    # key transform - forms.IntegerField cleans into a plain int instead, which it can.
    field_class = forms.IntegerField


def context_filter(key, filter_cls=_JSONContextNumberFilter, **kwargs):
    """
    Filter over a key in pghistory's context JSON - e.g. `context_filter("org_id")`.

    Defaults to an integer-typed filter, since that's the common case (an auto-incrementing user
    pk). A project whose actor pks are some other type (e.g. UUID) passes its own `filter_cls`
    (any django-filter `Filter` subclass) rather than isik guessing - override the built-in
    `"actor"` entry the same way any other filter is overridden:

        extra_history_filters = {"actor": context_filter("user", filter_cls=CharFilter)}
    """
    kwargs.setdefault("field_name", f"pgh_context__{key}")
    return filter_cls(**kwargs)


def _context_field(event_model, name):
    for cf in getattr(event_model, "pgh_context_fields", ()):
        if cf.name == name:
            return cf
    return None


class _ContextFieldFilterMixin:
    # pghistory's own Events.objects.across(event_model) can't reference event_model's real
    # column directly - even pghistory.ProxyField on an Events subclass refuses anything but a
    # pgh_context__* path (RuntimeError otherwise), so a real ContextField column is out of its
    # reach entirely. Matching against event_model's own table first, then narrowing the aggregate
    # queryset to those pgh_ids, works with that restriction instead of fighting it.
    def __init__(self, *, event_model, **kwargs):
        self._event_model = event_model
        super().__init__(**kwargs)

    def filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs
        lookup = f"{self.field_name}__{self.lookup_expr}"
        matching_ids = self._event_model.objects.filter(**{lookup: value}).values("pgh_id")
        return qs.filter(pgh_id__in=matching_ids)


def context_field_filter(event_model, name, filter_cls=NumberFilter, **kwargs):
    """
    Filter over a `ContextField`'s own real, indexed column - e.g.
    `context_field_filter(event_model_for(Widget), "actor")` - instead of a `pgh_context` JSON
    lookup (`context_filter()`). Correct for whatever type the column actually is, uses its index,
    and needs no `pghistory.middleware.HistoryMiddleware` (a `ContextField` is stamped by
    `pghistory.context()` directly, with or without it).

    Defaults to an integer-typed filter, since that's the common case (an auto-incrementing pk).
    A `ContextField` of another type (e.g. UUID) passes its own `filter_cls` (any django-filter
    `Filter` subclass) rather than isik guessing - same escape hatch as `context_filter()`.
    """
    kwargs.setdefault("field_name", name)
    built_cls = type(f"_ContextField{filter_cls.__name__}", (_ContextFieldFilterMixin, filter_cls), {})
    return built_cls(event_model=event_model, **kwargs)


class _StrictFilterSet(FilterSet):
    """
    Raises instead of silently ignoring a value that fails a declared filter's own validation
    (e.g. `?created_after=not-a-date`, or `?actor=<uuid>` against the integer-typed default) -
    django-filter's own default just drops the offending value from `cleaned_data` and filters on
    whatever is left, which reads as "found nothing more to filter" rather than "this parameter
    was rejected". A caller passing a bad filter value gets a 400 naming it instead of a
    200 they'd have to notice was wrongly unfiltered.
    """

    def filter_queryset(self, queryset):
        if self.form.errors:
            raise ValidationError(self.form.errors)
        return super().filter_queryset(queryset)


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
    context user) - filtering on an indexed real column via `context_field_filter()` if the
    tracked model has an `actor` `ContextField`, else on `pgh_context` JSON via `context_filter()`
    if `pghistory.middleware.HistoryMiddleware` (or a subclass) is installed, else omitted
    entirely.

    Add your own filters via `extra_history_filters` - merged on top of the built-ins, so a
    matching key overrides one and a new key just adds one:

        class OrgWidgetViewSet(HistoryMixin, BaseModelViewSet):
            ...
            extra_history_filters = {"org_id": context_filter("org_id")}

    Override `default_history_filters()` instead to replace the built-in set entirely -
    `extra_history_filters` still layers on top of whatever that returns.

    `history_withhold` names tracked fields to keep out of both endpoints' output entirely, while
    still recording them - see `generic_history_serializer()`'s own `withhold=` for what that
    means for `changes`.

    `history_list_scoped_to_queryset` (default `False`, preserving today's behavior) restricts
    `GET <endpoint>/history/` to events for objects `self.get_queryset()` would return, instead of
    every instance of the model regardless of scope. Leave it off when `history_list_permission_
    classes` is already the intended security boundary (e.g. `IsSuperUser` seeing everything is
    the point); turn it on when a viewset's `get_queryset()` is itself the boundary (e.g. scoped to
    the caller's own organization) - without it, the cross-object endpoint answers for objects the
    per-object one would 404 on.
    """

    extra_history_filters = {}
    history_list_permission_classes = [IsSuperUser]
    history_withhold = ()
    history_list_scoped_to_queryset = False

    @classmethod
    def default_history_filters(cls):
        event_model = event_model_for(cls.model)
        filters = {
            "action": ChoiceFilter(
                field_name="pgh_label", choices=[("insert", "insert"), ("update", "update"), ("delete", "delete")]
            ),
            "created_after": DateTimeFilter(field_name="pgh_created_at", lookup_expr="gte"),
            "created_before": DateTimeFilter(field_name="pgh_created_at", lookup_expr="lte"),
            "object_id": CharFilter(field_name="pgh_obj_id"),
        }
        # An "actor" ContextField's real column wins over the pgh_context JSON lookup, same
        # precedent as _history_base_queryset()'s own actor_id annotation below - and unlike that
        # JSON lookup, it doesn't need HistoryMiddleware, since a ContextField is stamped by
        # pghistory.context() directly.
        actor_name = "actor"
        if _context_field(event_model, actor_name) is not None:
            # django-filter's own FilterSetMetaclass backfills a filter's field_name from the
            # dict key it's assigned under (actor_name, on both sides here) whenever it comes out
            # falsy - so a wrong/missing name= passed to context_field_filter() is unobservable at
            # this specific call site (field_name= itself is tested directly on the function).
            filters[actor_name] = context_field_filter(event_model, actor_name)  # pragma: no mutate
        elif history_middleware_installed():
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
        built = type("AutoHistoryFilterSet", (_StrictFilterSet,), {**filters, "Meta": meta})
        cls._history_filterset_class = built
        return built

    @classproperty
    def history_serializer_class(cls):
        cached = cls.__dict__.get("_history_serializer_class")
        if cached is not None:
            return cached
        built = generic_history_serializer(cls.model, withhold=cls.history_withhold)
        cls._history_serializer_class = built
        return built

    def _history_base_queryset(self):
        event_model = event_model_for(self.model)
        queryset = Events.objects.across(event_model)
        context_field_names = getattr(event_model, "pgh_context_field_names", frozenset())
        if history_middleware_installed() and "actor_id" not in context_field_names:
            # A SQL-level annotation, not a serializer-side `source="pgh_context.user"` - pgh_context
            # is null for any event created outside a request, and KeyTransform resolves that to a
            # clean SQL NULL instead of the Python-side None a dotted source would crash on. Skipped
            # when a ContextField already put a real actor_id column on the event model - annotating
            # the same fact a second time from JSON would just be slower and redundant.
            queryset = queryset.annotate(actor_id=KeyTransform("user", "pgh_context"))
        return queryset

    def get_history_queryset(self, obj):
        return self._history_base_queryset().tracks(obj).order_by("-pgh_id")

    def get_all_history_queryset(self):
        queryset = self._history_base_queryset()
        if self.history_list_scoped_to_queryset:
            queryset = queryset.tracks(self.get_queryset())
        return queryset.order_by("-pgh_id")

    def _history_response(self, request, queryset):
        queryset = self.history_filterset_class(request.query_params, queryset=queryset).qs
        page = self.paginate_queryset(queryset)
        serializer = self.history_serializer_class(page if page is not None else queryset, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @action(detail=True, methods=["get"])
    def history(self, request, *args, **kwargs):
        """One object's history, paginated, newest first - see HistoryMixin's own docstring for query params."""
        # *args, **kwargs rather than a hardcoded pk=None: a viewset with a custom lookup_field
        # (e.g. lookup_field = "schema_name") is dispatched with that name instead, and
        # self.get_object() below reads it off self.kwargs, not this method's arguments. A
        # schema generator publishes this docstring verbatim as the operation's description, so
        # implementation rationale belongs in this comment, not there.
        return self._history_response(request, self.get_history_queryset(self.get_object()))

    @action(detail=False, methods=["get"], url_path="history", url_name="history-list")
    def history_list(self, request):
        """History across every instance of the model (or scoped to `self.get_queryset()` - see
        `history_list_scoped_to_queryset`), paginated, newest first, restricted by
        `history_list_permission_classes` - see HistoryMixin's own docstring for query params."""
        return self._history_response(request, self.get_all_history_queryset())

    def check_permissions(self, request):
        # In check_permissions() rather than get_permissions(): a viewset is far more likely to
        # override get_permissions() wholesale for unrelated reasons (a common pattern) than
        # check_permissions(), which DRF's dispatch() calls directly - so this can't be dropped
        # by a subclass overriding the more commonly-touched hook without realizing it drops this
        # too, and it can't silently fall open the way get_permissions() being replaced would.
        if self.action == "history_list":
            for permission in self.history_list_permission_classes:
                if not permission().has_permission(request, self):
                    self.permission_denied(
                        request,
                        message=getattr(permission, "message", None),
                        code=getattr(permission, "code", None),
                    )
            return
        super().check_permissions(request)

    def get_serializer_class(self):
        if self.action in ("history", "history_list"):
            return self.history_serializer_class
        return super().get_serializer_class()
