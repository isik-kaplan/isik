"""HistoryMixin + context_filter()/context_field_filter() - see each one's own docstring."""

from dataclasses import dataclass

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models import Q
from django.db.models.fields.json import KeyTransform
from django.utils.functional import classproperty
from django_filters.rest_framework import (
    CharFilter,
    ChoiceFilter,
    DateTimeFilter,
    FilterSet,
    NumberFilter,
    UUIDFilter,
)
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from pghistory.models import Events
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from isik._internal.translation import gettext as _
from isik.django.apps.common.db.history import event_model_for, history_middleware_installed
from isik.django.drf.filters import NarrowingFilterMixin
from isik.django.drf.permissions import IsSuperUser
from isik.django.drf.serializers.history import generic_history_serializer


class _JSONContextNumberFilter(NumberFilter):
    # NumberFilter's default field_class (forms.DecimalField) cleans values into Decimal, which
    # psycopg's JSON parameter adapter can't serialize when comparing against a pghistory context
    # key transform - forms.IntegerField cleans into a plain int instead, which it can.
    field_class = forms.IntegerField


class _JSONContextUUIDFilter(UUIDFilter):
    # A UUID in pghistory's context JSON is its text, and psycopg's JSON adapter can't serialize a
    # uuid.UUID - so the cleaned value is compared as the string it's stored as.
    def filter(self, qs, value):
        return super().filter(qs, None if value is None else str(value))


def _pk_filter_class(model):
    """The context filter for a key holding `model`'s pk: integer-typed for an integer pk, UUID otherwise."""
    if isinstance(model._meta.pk, models.IntegerField):
        return _JSONContextNumberFilter
    return _JSONContextUUIDFilter


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


class _ContextFieldFilterMixin(NarrowingFilterMixin):
    # pghistory's own Events.objects.across(event_model) can't reference event_model's real
    # column directly - even pghistory.ProxyField on an Events subclass refuses anything but a
    # pgh_context__* path (RuntimeError otherwise), so a real ContextField column is out of its
    # reach entirely. Matching against event_model's own table first, then narrowing the aggregate
    # queryset to those pgh_ids, works with that restriction instead of fighting it.
    def __init__(self, *, event_model, **kwargs):
        self._event_model = event_model
        super().__init__(**kwargs)

    def narrow(self, qs, value):
        lookup = f"{self.field_name}__{self.lookup_expr}"
        matching_ids = self._event_model.objects.filter(**{lookup: value}).values("pgh_id")
        return qs.filter(pgh_id__in=matching_ids)


def _column_filter(event_model, name):
    """
    (filter class, its options) for a `ContextField`'s column, as django-filter picks them for the
    column's own type - a foreign key's being the type of what it points at, so a UUID-keyed actor is
    compared as a UUID and an integer-keyed one as a number.
    """
    field = event_model._meta.get_field(name)
    column = field.target_field if field.is_relation else field
    made = FilterSet.filter_for_field(column, name)
    return type(made), {"field_name": made.field_name, **made.extra}


def context_field_filter(event_model, name, filter_cls=None, **kwargs):
    """
    Filter over a `ContextField`'s own real, indexed column - e.g.
    `context_field_filter(event_model_for(Widget), "actor")` - instead of a `pgh_context` JSON
    lookup (`context_filter()`). Uses its index, and needs no `pghistory.middleware.HistoryMiddleware`
    (a `ContextField` is stamped by `pghistory.context()` directly, with or without it).

    Typed after the column itself: django-filter's filter for its type, a foreign key's being that of
    what it points at - a UUID column (or a key to a UUID pk) takes a UUID, an integer one a number.
    Pass `filter_cls` (any django-filter `Filter` subclass) to pick another.
    """
    if filter_cls is None:
        filter_cls, options = _column_filter(event_model, name)
        kwargs = {**options, **kwargs}
    else:
        kwargs.setdefault("field_name", name)
    built_cls = type(f"_ContextField{filter_cls.__name__}", (_ContextFieldFilterMixin, filter_cls), {})
    return built_cls(event_model=event_model, **kwargs)


class _StrictFilterSet(FilterSet):
    """
    Raises instead of silently ignoring a value that fails a declared filter's own validation
    (e.g. `?created_after=not-a-date`, or `?actor=abc` against an integer actor) -
    django-filter's own default just drops the offending value from `cleaned_data` and filters on
    whatever is left, which reads as "found nothing more to filter" rather than "this parameter
    was rejected". A caller passing a bad filter value gets a 400 naming it instead of a
    200 they'd have to notice was wrongly unfiltered.
    """

    def filter_queryset(self, queryset):
        if self.form.errors:
            raise ValidationError(self.form.errors)
        return super().filter_queryset(queryset)


@dataclass(frozen=True)
class HistoryStream:
    """
    Another model's history, part of an object's - declared in `HistoryMixin.history_also`. `model` is
    tracked (`@track_events()`); `path` is its field pointing at the object (a foreign key or
    one-to-one), by the name a reader says; `serializer`, if given, is what of its columns a row shows -
    in that row's `changes`, under the serializer's names. Without one, a row shows none of them.
    """

    model: type
    path: str | None = None
    serializer: type | None = None


def _json_value(pk):
    # How pghistory's snapshot holds a key: an integer as a number, anything else (a UUID) as its text.
    return pk if isinstance(pk, int) else str(pk)


class _ObjectFilter(NarrowingFilterMixin, CharFilter):
    """`object_id` over several streams: the object a row is about - see `HistoryMixin.history_also`."""

    def __init__(self, *, view, **kwargs):
        self.view = view
        super().__init__(**kwargs)

    def narrow(self, qs, value):
        try:
            pk = self.view.model._meta.pk.to_python(value)
        except DjangoValidationError:
            raise ValidationError({"object_id": [_("Not a valid key.")]}) from None
        return self.view.history_about(qs, [pk])


class _PartFilter(NarrowingFilterMixin, ChoiceFilter):
    """`part` - the rows of one stream of an object's history, by its key."""

    def __init__(self, *, view, **kwargs):
        self.view = view
        super().__init__(**kwargs)

    def narrow(self, qs, value):
        return qs.filter(pgh_model=self.view.history_stream_labels()[value])


class _RowField(serializers.Field):
    """A read-only field of the whole history row, computed by `value(event, context)`."""

    def __init__(self, *, value, **kwargs):
        self.value = value
        super().__init__(source="*", read_only=True, **kwargs)

    def to_representation(self, event):
        return self.value(event, self.context)


@extend_schema_field(OpenApiTypes.STR)
class _TextRowField(_RowField):
    pass


@extend_schema_field(OpenApiTypes.OBJECT)
class _ChangesRowField(_RowField):
    pass


# What every stream's row has of its own - the rest of the owner's fields are null on another stream's row.
_ROW_FIELDS = ("event_id", "event_created_at", "action", "changes", "part", "subject_id", "subject_label", "detail")


def _nullable_owner_fields(own):
    # The owner serializer's own fields alone - not a row field, nor one history_serializer_bases adds.
    owned = set(own._declared_fields) - set(_ROW_FIELDS)

    def get_fields(self):
        fields = own.get_fields(self)
        for name in owned:
            fields[name].allow_null = True
        return fields

    return get_fields


def _subject_of(paths):
    """The object a row is about: the owner's id, on every stream's row - see `history_also`."""

    def subject(event, context=None):
        path = paths[event.pgh_model]
        value = event.pgh_obj_id if path is None else (event.pgh_data or {}).get(path)
        return None if value is None else str(value)

    return subject


def _changes_of(renderers):
    # Each row's changes through its own stream's serializer - never another's, whose field names a part
    # model may share. A part without a serializer shows none.
    def changes(event, context):
        renderer = renderers.get(event.pgh_model)
        if renderer is None or event.pgh_diff is None:
            return None
        return renderer.fields["changes"].to_representation(event.pgh_diff)

    return changes


def _subject_label_of(subject):
    def label(event, context):
        return context.get("history_page", {}).get("subject_labels", {}).get(subject(event))

    return label


def _detail(event, context):
    found = context.get("history_page", {}).get("details", {}).get(event.pgh_model)
    if found is None:
        return None
    attname, values = found
    # A null key is "None", which no pk is.
    return values.get(str((event.pgh_data or {}).get(attname)))


class HistoryMixin:
    """
    Adds two endpoints to a `BaseModelViewSet` for a model tracked with `@track_events()`, both
    paginated (via the viewset's own `pagination_class`), newest first, using an auto-built
    serializer over the model's history (see `generic_history_serializer()`). No other
    configuration required - everything is resolved from `self.model` and the viewset's own
    serializer.

    The history shows what the resource shows: the fields of the serializer `retrieve` uses
    (`serializer_class_action_map["retrieve"]`, else `serializer_class`), each under the same name
    and rendered the same way. A column that serializer leaves out is left out of the history too,
    and an update that changed only such columns isn't listed - so forgetting something hides it,
    rather than serving it.

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
        # (whichever of WidgetSerializer's fields render one column - see generic_history_serializer())

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

    `history_shows_change_of` names hidden fields whose changes should still be listed, as
    `[None, None]` - "the password changed at 14:02", never the hash:

        class UserViewSet(HistoryMixin, BaseModelViewSet):
            ...
            history_shows_change_of = ["password"]

    `history_ignores_change_of` names fields whose change alone doesn't list an update, on top of the
    ones the row keeps current itself - `updated_at`, which `BaseModel`'s trigger bumps on every
    update, and any `auto_now` field. Without that, every save would be listed, a sign-in's
    `last_login` write included. They're still shown, and in `changes` alongside a real change.

    Override `history_source_serializer()` when the history should follow a serializer other than
    the one `retrieve` uses.

    `scope_history(events)` narrows the events either endpoint returns, per request - every history
    query passes through it, including one from an overridden `get_history_queryset()`. Access to an
    object is the viewset's own business (`get_queryset()`, permissions); this is for rules about the
    events themselves, e.g. only those recorded while the caller owned the object:

        def scope_history(self, events):
            if self.request.user.is_staff:
                return events
            # A snapshot holds a UUID as its text.
            return events.filter(pgh_data__owner_id=str(self.request.user.pk))

    `history_list_scoped_to_queryset` (default `False`, preserving today's behavior) restricts
    `GET <endpoint>/history/` to events for objects `self.get_queryset()` would return, instead of
    every instance of the model regardless of scope. Leave it off when `history_list_permission_
    classes` is already the intended security boundary (e.g. `IsSuperUser` seeing everything is
    the point); turn it on when a viewset's `get_queryset()` is itself the boundary (e.g. scoped to
    the caller's own organization) - without it, the cross-object endpoint answers for objects the
    per-object one would 404 on.

    `history_serializer_bases` (default `()`) are serializer classes mixed into the one built - each
    row's own fields a project adds, read from `context["history_page"]` that `history_page_context()`
    fills once per page:

        class ActorLabelSerializer(serializers.Serializer):
            actor_label = serializers.SerializerMethodField()

            def get_actor_label(self, event) -> str | None:
                return self.context["history_page"]["actors"].get(event.actor_id)

        class WidgetViewSet(HistoryMixin, BaseModelViewSet):
            history_serializer_bases = (ActorLabelSerializer,)

    The class keeps the built one's name, and is cached with it under `history_serializer_cache_key()`.

    `history_component_prefix` (default `""`) goes in front of the generated serializer's name, and so
    of its schema component. A second `HistoryMixin` viewset over the same model needs one - two
    `WidgetHistory` components would collide, and the schema would keep only one of their shapes:

        class StaffWidgetViewSet(HistoryMixin, BaseModelViewSet):
            ...
            history_component_prefix = "Staff"   # StaffWidgetHistory, not WidgetHistory
    """

    extra_history_filters = {}
    # {key: HistoryStream} - other models' histories, part of this one's. Declared, never derived.
    history_also = {}
    # {part key: (foreign key of that part's model, attribute of the row it points at)}
    history_detail = {}
    history_list_permission_classes = [IsSuperUser]
    history_shows_change_of = ()
    history_ignores_change_of = ()
    history_list_scoped_to_queryset = False
    history_component_prefix = ""
    # Serializer classes mixed into the history serializer this view builds - fields of a project's own.
    history_serializer_bases = ()

    @classmethod
    def _refuse_wrong_parts(cls):
        # When the view is first routed - DRF's router reads both built classes - rather than at the
        # first request for its history.
        problems = history_part_problems(cls)
        if problems:
            raise ImproperlyConfigured(" ".join(problems))

    @classmethod
    def default_history_filters(cls):
        cls._refuse_wrong_parts()
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
            filters[actor_name] = context_field_filter(event_model, actor_name)
        elif history_middleware_installed():
            # Typed after the user model's pk - a UUID pk compared as a number would refuse every value.
            filters["actor"] = context_filter("user", filter_cls=_pk_filter_class(get_user_model()))
        if cls.history_also:
            filters["object_id"] = _ObjectFilter(view=cls)
            filters["part"] = _PartFilter(choices=[(key, key) for key in cls.history_parts()], view=cls)
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

    @classmethod
    def history_source_serializer(cls):
        """The serializer whose fields the history shows - by default the one `retrieve` uses."""
        return cls.serializer_class_action_map.get("retrieve", cls.serializer_class)

    @classmethod
    def history_serializer_cache_key(cls):
        """
        What tells two history serializers this view builds apart - one by default. Override when the
        history differs by something the class alone doesn't say (a schema per surface, a language), so
        each gets its own.
        """
        return None

    @classproperty
    def history_serializer_class(cls):
        cached = cls.__dict__.get("_history_serializer_classes", {})
        key = cls.history_serializer_cache_key()
        if key in cached:
            return cached[key]
        cls._refuse_wrong_parts()
        built = generic_history_serializer(
            cls.model,
            cls.history_source_serializer(),
            shows_change_of=cls.history_shows_change_of,
            ignores_change_of=cls.history_ignores_change_of,
            name=f"{cls.history_component_prefix}{cls.model.__name__}HistorySerializer",
        )
        if cls.history_also:
            built = cls._streams_serializer(built)
        if cls.history_serializer_bases:
            # Under the built one's name, so its schema component keeps it.
            built = type(built.__name__, (*cls.history_serializer_bases, built), {})
        # On cls.__dict__, not a base class's - a subclass builds its own, as for the filterset.
        cls._history_serializer_classes = {**cached, key: built}
        return built

    @classmethod
    def history_parts(cls):
        """{key: HistoryStream} - this model's own stream under its model name, then `history_also`'s."""
        return {cls.model._meta.model_name: HistoryStream(cls.model), **cls.history_also}

    @classmethod
    def history_stream_labels(cls):
        """{part key: its event model's label} - what a row names its stream by (`pgh_model`)."""
        return {key: event_model_for(stream.model)._meta.label for key, stream in cls.history_parts().items()}

    @classmethod
    def _path_columns(cls):
        """{event model label: the snapshot column naming the owner} - None for the own stream."""
        labels = cls.history_stream_labels()
        columns = {labels[cls.model._meta.model_name]: None}
        for key, stream in cls.history_also.items():
            columns[labels[key]] = stream.model._meta.get_field(stream.path).column
        return columns

    @classmethod
    def _streams_serializer(cls, own):
        labels = cls.history_stream_labels()
        renderers = {labels[cls.model._meta.model_name]: own}
        for key, stream in cls.history_also.items():
            if stream.serializer is not None:
                # Only renders a part's changes - never a schema component, so its name needn't be unique.
                renderers[labels[key]] = generic_history_serializer(stream.model, stream.serializer)
        subject = _subject_of(cls._path_columns())
        keys = {label: key for key, label in labels.items()}
        attrs = {
            "changes": _ChangesRowField(
                value=_changes_of({label: each() for label, each in renderers.items()}), allow_null=True
            ),
            "part": _TextRowField(value=lambda event, context: keys[event.pgh_model]),
            "subject_id": _TextRowField(value=subject, allow_null=True),
            "subject_label": _TextRowField(value=_subject_label_of(subject), allow_null=True),
            "detail": _TextRowField(value=_detail, allow_null=True),
            "get_fields": _nullable_owner_fields(own),
            "stream_serializers": renderers,
            "subject_of": staticmethod(subject),
        }
        return type(own.__name__, (own,), attrs)

    @classmethod
    def _needs_exact_scope(cls):
        """
        Whether `references()` alone would take in rows the declaration didn't name. pghistory's
        `references(obj)` matches every column of an event table pointing at the owner's model - so
        a stream with more than one (a self foreign key on the owner; a part model pointing at it
        twice) needs each row narrowed to its declared path. Otherwise `references()` is exact.
        """
        for stream in cls.history_parts().values():
            event_model = event_model_for(stream.model)
            if sum(field.related_model is cls.model for field in event_model._meta.fields) > 1:
                return True
        return False

    @classmethod
    def history_about(cls, events, pks):
        """`events` narrowed to the rows about the objects `pks` - see `history_also`."""
        if not pks:
            return events.none()
        events = events.references(*[cls.model(pk=pk) for pk in pks])
        if not cls._needs_exact_scope():
            return events
        return events.filter(cls.history_scope(pks))

    @classmethod
    def history_scope(cls, pks):
        """The rows about the objects `pks`, each stream's by its own declared path - a `Q`."""
        labels = cls.history_stream_labels()
        scope = Q(pgh_model=labels[cls.model._meta.model_name], pgh_obj_id__in=[str(pk) for pk in pks])
        for key, stream in cls.history_also.items():
            column = stream.model._meta.get_field(stream.path).column
            scope |= Q(pgh_model=labels[key], **{f"pgh_data__{column}__in": [_json_value(pk) for pk in pks]})
        return scope

    def _history_base_queryset(self):
        if self.history_also:
            return self._streams_base_queryset()
        event_model = event_model_for(self.model)
        # An update that changed nothing the history shows isn't listed: it would only say when a
        # hidden column changed, or that the row restamped its own updated_at.
        queryset = Events.objects.across(event_model).filter(
            ~Q(pgh_label="update") | Q(pgh_diff__has_any_keys=list(self.history_serializer_class.history_columns))
        )
        return self._with_actor(queryset, event_model)

    def _streams_base_queryset(self):
        renderers = self.history_serializer_class.stream_serializers
        listed = Q()
        for label in self.history_stream_labels().values():
            renderer = renderers.get(label)
            if renderer is None:
                # A part showing no columns lists every event of its own: the declaration says they matter.
                listed |= Q(pgh_model=label)
            else:
                listed |= Q(pgh_model=label) & (
                    ~Q(pgh_label="update") | Q(pgh_diff__has_any_keys=list(renderer.history_columns))
                )
        event_models = [event_model_for(stream.model) for stream in self.history_parts().values()]
        # Rows of several tables, so no one table's actor column is all of theirs - always the context's.
        return self._with_actor(Events.objects.across(*event_models).filter(listed), None)

    def _with_actor(self, queryset, event_model):
        context_field_names = getattr(event_model, "pgh_context_field_names", frozenset())
        if history_middleware_installed() and "actor_id" not in context_field_names:
            # A SQL-level annotation, not a serializer-side `source="pgh_context.user"` - pgh_context
            # is null for any event created outside a request, and KeyTransform resolves that to a
            # clean SQL NULL instead of the Python-side None a dotted source would crash on. Skipped
            # when a ContextField already put a real actor_id column on the event model - annotating
            # the same fact a second time from JSON would just be slower and redundant.
            queryset = queryset.annotate(actor_id=KeyTransform("user", "pgh_context"))
        return queryset

    def _history_order(self, queryset):
        # pgh_id is a sequence per event table, so across several it interleaves them by how many rows
        # each ever had. Time orders a merged feed; within one table pgh_id follows commit order, where
        # pgh_created_at - the transaction's start - may not.
        if self.history_also:
            return queryset.order_by("-pgh_created_at", "-pgh_id")
        return queryset.order_by("-pgh_id")

    def get_history_queryset(self, obj):
        queryset = self._history_base_queryset()
        if self.history_also:
            return self._history_order(self.history_about(queryset, [obj.pk]))
        return self._history_order(queryset.tracks(obj))

    def get_all_history_queryset(self):
        queryset = self._history_base_queryset()
        if self.history_list_scoped_to_queryset:
            if self.history_also:
                pks = [pk for (pk,) in self.get_queryset().values_list("pk")]
                queryset = self.history_about(queryset, pks)
            else:
                queryset = queryset.tracks(self.get_queryset())
        return self._history_order(queryset)

    def history_page_context(self, rows):
        """
        What the page's rows need resolved once, rather than once each - handed to the history
        serializer as `context["history_page"]`. `rows` is the page, read into a list. By default, over
        several streams: `subject_labels`, each subject's `str()` through `get_queryset()` - so a subject
        the caller can't see, or one that's gone, stays unnamed - and `details`, `history_detail`'s
        values. Override with `super()` to add a project's own (actors, say).
        """
        if not self.history_also:
            return {}
        subject_of = self.history_serializer_class.subject_of
        subjects = {subject_of(row) for row in rows} - {None}
        labels = self.history_stream_labels()
        details = {}
        for key, (field_name, attribute) in self.history_detail.items():
            pointing = self.history_parts()[key].model._meta.get_field(field_name)
            pointed = {(row.pgh_data or {}).get(pointing.attname) for row in rows if row.pgh_model == labels[key]}
            related = pointing.related_model._base_manager.filter(pk__in=pointed - {None})
            details[labels[key]] = (pointing.attname, {str(obj.pk): getattr(obj, attribute) for obj in related})
        visible = self.get_queryset().filter(pk__in=subjects)
        return {"subject_labels": {str(obj.pk): str(obj) for obj in visible}, "details": details}

    def scope_history(self, events):
        """The events this request may see, from both endpoints - all of them unless overridden."""
        return events

    def _history_response(self, request, queryset):
        queryset = self.scope_history(queryset)
        queryset = self.history_filterset_class(request.query_params, queryset=queryset).qs
        page = self.paginate_queryset(queryset)
        # Read into a list once, so what the page resolves (history_page_context) and what it serializes
        # are the same rows.
        rows = list(page if page is not None else queryset)
        context = {**self.get_serializer_context(), "history_page": self.history_page_context(rows)}
        serializer = self.history_serializer_class(rows, many=True, context=context)
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


def _same_model(one, other):
    return one._meta.concrete_model is other._meta.concrete_model


def _stream_problems(view, key, stream):
    where = {"view": view.__qualname__, "key": key}
    if not isinstance(stream, HistoryStream):
        return [_("%(view)s.history_also[%(key)r] is %(value)r, not a HistoryStream.") % {**where, "value": stream}]
    if "pgh_event_model" not in dir(stream.model):
        return [
            _("%(view)s.history_also[%(key)r]: %(model)s isn't tracked - give it @track_events().")
            % {**where, "model": stream.model._meta.label}
        ]
    fields = {field.name: field for field in stream.model._meta.concrete_fields}
    path = fields.get(stream.path)
    if path is None or not path.is_relation or not _same_model(path.related_model, view.model):
        return [
            _("%(view)s.history_also[%(key)r]: %(path)r isn't a foreign key or one-to-one of %(model)s to %(owner)s.")
            % {**where, "path": stream.path, "model": stream.model._meta.label, "owner": view.model._meta.label}
        ]
    event_model = event_model_for(stream.model)
    if path.column not in {field.column for field in event_model._meta.concrete_fields}:
        return [
            _("%(view)s.history_also[%(key)r]: %(events)s doesn't record %(path)r - track it in the history.")
            % {**where, "events": event_model._meta.label, "path": stream.path}
        ]
    return []


def _detail_problems(view, key, detail):
    where = {"view": view.__qualname__, "key": key}
    stream = view.history_also.get(key)
    if not isinstance(stream, HistoryStream):
        return [_("%(view)s.history_detail[%(key)r] names no stream of history_also.") % where]
    if not (isinstance(detail, tuple) and len(detail) == 2):
        return [
            _("%(view)s.history_detail[%(key)r] is %(value)r, not (foreign key, attribute).")
            % {**where, "value": detail}
        ]
    field_name, attribute = detail
    fields = {field.name: field for field in stream.model._meta.concrete_fields}
    pointing = fields.get(field_name)
    if pointing is None or not pointing.is_relation:
        return [
            _("%(view)s.history_detail[%(key)r]: %(field)r isn't a foreign key of %(model)s.")
            % {**where, "field": field_name, "model": stream.model._meta.label}
        ]
    if not hasattr(pointing.related_model, attribute):
        return [
            _("%(view)s.history_detail[%(key)r]: %(model)s has no %(attribute)r.")
            % {**where, "model": pointing.related_model._meta.label, "attribute": attribute}
        ]
    return []


def history_part_problems(view):
    """
    What's wrong with `view`'s `history_also` and `history_detail`, one sentence each - `[]` when
    they can be served. A stream must be a `HistoryStream` of a tracked model whose `path` - a foreign
    key or one-to-one to the view's model - its history records, under a key other than the view's own
    stream's; a detail must name one of those streams, a foreign key of its model, and an attribute of
    what that points at.
    """
    problems = []
    own = view.model._meta.model_name
    if own in view.history_also:
        problems.append(
            _("%(view)s.history_also[%(key)r] takes the key of %(model)s's own stream - name it otherwise.")
            % {"view": view.__qualname__, "key": own, "model": view.model._meta.label}
        )
    for key, stream in view.history_also.items():
        problems.extend(_stream_problems(view, key, stream))
    for key, detail in view.history_detail.items():
        problems.extend(_detail_problems(view, key, detail))
    return problems
