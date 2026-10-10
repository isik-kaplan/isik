"""generic_history_serializer() - see its own docstring."""

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rest_framework import serializers
from rest_framework.relations import PKOnlyObject, RelatedField

from isik._internal.translation import gettext as _
from isik.django.apps.common.db.history import event_model_for, history_middleware_installed


_FIELD_TYPES = {
    models.CharField: serializers.CharField,
    models.TextField: serializers.CharField,
    models.SlugField: serializers.SlugField,
    models.EmailField: serializers.EmailField,
    models.URLField: serializers.URLField,
    models.BooleanField: serializers.BooleanField,
    models.IntegerField: serializers.IntegerField,
    models.BigIntegerField: serializers.IntegerField,
    models.SmallIntegerField: serializers.IntegerField,
    models.PositiveIntegerField: serializers.IntegerField,
    models.PositiveSmallIntegerField: serializers.IntegerField,
    models.AutoField: serializers.IntegerField,
    models.BigAutoField: serializers.IntegerField,
    models.SmallAutoField: serializers.IntegerField,
    models.FloatField: serializers.FloatField,
    models.DecimalField: serializers.DecimalField,
    models.DateTimeField: serializers.DateTimeField,
    models.DateField: serializers.DateField,
    models.TimeField: serializers.TimeField,
    models.DurationField: serializers.DurationField,
    models.UUIDField: serializers.UUIDField,
    models.JSONField: serializers.JSONField,
}

# event_id/event_created_at (not id/created_at) - a model built on BaseModel already has its own
# id/created_at among the fields a serializer shows, and those have to win: they're the real object's
# identity/timestamp at that point in history, not metadata about the history record itself.
_META_FIELD_NAMES = {"event_id", "event_created_at", "action", "changes", "actor_id"}


class _ReadsTheSnapshot:
    """Mixed into a copy of one of the resource serializer's own fields, so it renders exactly as it
    does on the resource - same type, same format - but reads its value out of an event's `pgh_data`
    snapshot instead of off a live object. `snapshot_of` is `(event_model, field name)` rather than
    the field itself: DRF rebuilds a field from its kwargs on every deepcopy, and a model class
    copies as itself."""

    def __init__(self, *args, snapshot_of, **kwargs):
        self.snapshot_of = snapshot_of
        super().__init__(*args, **kwargs)

    def value_of(self, raw):
        event_model, name = self.snapshot_of
        value = event_model._meta.get_field(name).to_python(raw)
        # A relation shows its primary key only (see _renders_one_column()), which DRF's related
        # fields read off a PKOnlyObject - the related row as it is today is never fetched.
        return PKOnlyObject(pk=value) if isinstance(self, RelatedField) else value

    def reads_another_event_model(self, event):
        """Whether this row was recorded by a different event model than this field reads.

        `pgh_model` is a column of `pghistory.models.Events`, which is what a feed over more than one
        tracked model selects from; a queryset of one concrete event model has no such attribute, and
        every row in it belongs to this field.
        """
        event_model, _name = self.snapshot_of
        recorded_by = getattr(event, "pgh_model", None)
        return recorded_by is not None and recorded_by != event_model._meta.label

    def get_attribute(self, event):
        """A null comes back as None (or a PKOnlyObject of None), which DRF renders as null itself.

        A row from another event model renders null rather than raising: this field is about one
        model's column, and a row recorded for a different one has nothing to say about it. A row of
        this model whose snapshot is missing the column still raises, because that means the
        snapshot and the field disagree about what is tracked, which is worth hearing about.
        """
        if self.reads_another_event_model(event):
            return None
        event_model, name = self.snapshot_of
        return self.value_of(event.pgh_data[event_model._meta.get_field(name).column])

    def render(self, raw):
        """One raw snapshot value, as the resource would render it."""
        return None if raw is None else self.to_representation(self.value_of(raw))


def _reading_the_snapshot(field, event_model, name):
    field_cls = type(field)
    reading_cls = type(field_cls.__name__, (_ReadsTheSnapshot, field_cls), {})
    return reading_cls(*field._args, snapshot_of=(event_model, name), **field._kwargs)


class _ChangesField(serializers.JSONField):
    """`pgh_diff`, keyed and rendered like the rest of the history: a column the resource shows
    appears under the serializer's name for it, its `[old, new]` rendered by that field; a column
    named in `shows_change_of` appears as `[None, None]`, so the change is visible but neither value
    is; every other column - a `ContextField` included, since who acted isn't a change to the object -
    is left out."""

    def __init__(self, *, shown, marked, **kwargs):
        self._shown = shown
        self._marked = marked
        super().__init__(**kwargs)

    def to_representation(self, value):
        result = {}
        for column, change in super().to_representation(value).items():
            if column in self._shown:
                field = self.parent.fields[self._shown[column]]
                result[self._shown[column]] = [field.render(raw) for raw in change]
            elif column in self._marked:
                result[self._marked[column]] = [None, None]
        return result or None


def _renders_one_column(field, source, event_field):
    """Whether `field`, reading `source`, renders `event_field`'s own value and nothing else."""
    if source == event_field.attname:
        return not isinstance(field, RelatedField)
    # The relation by its own name: only its primary key can be rendered without reading the related
    # row, which would be today's row presented as history.
    return isinstance(field, RelatedField) and field.use_pk_only_optimization()


def _shown_fields(serializer, tracked):
    """{output name: (serializer field, event model field)} for every field `serializer` reads that
    renders one tracked column. A method field, a nested serializer, a dotted source or `"*"` can't
    be rebuilt from a snapshot, so the history leaves it out."""
    by_attribute = {}
    for event_field in tracked.values():
        by_attribute[event_field.name] = event_field
        by_attribute[event_field.attname] = event_field
    shown = {}
    for name, field in serializer().fields.items():
        if field.write_only or len(field.source_attrs) != 1:
            continue
        source = field.source_attrs[0]
        event_field = by_attribute.get(source)
        if event_field is not None and _renders_one_column(field, source, event_field):
            shown[name] = (field, event_field)
    return shown


def generic_history_serializer(model, serializer, *, shows_change_of=(), name=None):
    """
    Builds a read-only `Serializer` for the history of a model tracked with `@track_events()`,
    showing what `serializer` - the resource's own serializer - shows, and nothing else. History is
    the resource over time, so it never shows a column the resource doesn't: a field left out of
    `serializer` is left out of the history too, with nothing to keep in step.

        WidgetHistorySerializer = generic_history_serializer(Widget, WidgetSerializer)
        WidgetHistorySerializer(some_queryset, many=True).data

    Each entry has `event_id`, `event_created_at`, `action` ("insert"/"update"/"delete") and
    `changes`, then every field of `serializer` that renders one tracked column, under the
    serializer's name for it and rendered by the serializer's own field - so a history entry reads
    like the resource did at that moment. That covers a plain column (`name`, or `title` with
    `source="name"`) and a relation as its primary key (`PrimaryKeyRelatedField`, or `owner_id`). A
    method field, a nested serializer, a dotted source (`owner.name`) or `source="*"` is left out:
    it would read today's related rows or computed state, and present it as history.

    `changes` is `{name: [old, new]}` over the same fields, for whatever changed since the object's
    previous event (`None` on insert). Computed in SQL by `pghistory.models.Events`.

    `shows_change_of` names tracked model fields the resource hides whose changes should still
    appear, as `[None, None]` - the event shows that the password changed, never either hash:

        generic_history_serializer(User, UserSerializer, shows_change_of=["password"])
        # {"event_id": 4, "action": "update", "changes": {"password": [None, None]}, ...}

    A real `ContextField` column (`track_events(context_fields=[...])`) is shown under its own name
    - it records who acted, not a field of the object - and `actor_id` is added if
    `pghistory.middleware.HistoryMiddleware` (or a subclass) is installed and no `ContextField`
    already provides it.

    The class carries `history_columns`, the tracked columns whose change shows in `changes` -
    `HistoryMixin` leaves out an update that changed none of them.

    `name=` overrides the generated class name (default `<Model>HistorySerializer`).

    Raises `ImproperlyConfigured` if a shown field is named `event_id`/`event_created_at`/`action`/
    `changes`/`actor_id` or after a `ContextField`, and if `shows_change_of` names a field that isn't
    tracked or that the serializer already shows.
    """
    event_model = event_model_for(model)
    context_fields = getattr(event_model, "pgh_context_fields", ())
    context_names = {context_field.name for context_field in context_fields}
    tracked = {
        field.name: field
        for field in event_model._meta.fields
        if not field.name.startswith("pgh_") and field.name not in context_names
    }
    shown = _shown_fields(serializer, tracked)
    context = _context_fields(event_model, context_names)

    collisions = shown.keys() & (_META_FIELD_NAMES | context.keys())
    if collisions:
        raise ImproperlyConfigured(
            _(
                "%(serializer)s has field(s) named %(fields)s, which the history of %(model)s uses for "
                "its own - rename them in the serializer, or give the history a serializer of its own."
            )
            % {"serializer": serializer.__name__, "fields": sorted(collisions), "model": model.__name__}
        )

    shown_names = {event_field.name for _field, event_field in shown.values()}
    marked = {}
    for field_name in shows_change_of:
        if field_name not in tracked or field_name in shown_names:
            raise ImproperlyConfigured(
                _(
                    "shows_change_of names %(field)r, which isn't a tracked field of %(model)s that "
                    "%(serializer)s hides - list only fields the history would otherwise leave out."
                )
                % {"field": field_name, "model": model.__name__, "serializer": serializer.__name__}
            )
        marked[tracked[field_name].column] = field_name

    attrs = {
        "event_id": serializers.IntegerField(source="pgh_id", read_only=True),
        "event_created_at": serializers.DateTimeField(source="pgh_created_at", read_only=True),
        "action": serializers.CharField(source="pgh_label", read_only=True),
        "changes": _ChangesField(
            source="pgh_diff",
            read_only=True,
            allow_null=True,
            shown={event_field.column: output_name for output_name, (_field, event_field) in shown.items()},
            marked=marked,
        ),
        **{
            output_name: _reading_the_snapshot(field, event_model, event_field.name)
            for output_name, (field, event_field) in shown.items()
        },
        **context,
        "history_columns": frozenset([*(event_field.column for _field, event_field in shown.values()), *marked]),
    }
    if history_middleware_installed() and "actor_id" not in context:
        # A queryset-level annotation (see HistoryMixin._history_base_queryset) rather than sourced
        # off pgh_context directly - pgh_context is null for any event that wasn't created inside a
        # request (a migration, a shell, a background job), and a plain `source="pgh_context.user"`
        # would crash DRF's attribute traversal on that None instead of quietly serializing null.
        # Skipped when a ContextField already put a real actor_id column there - that's typed and
        # indexed, this JSON fallback is neither.
        attrs["actor_id"] = serializers.CharField(read_only=True, allow_null=True)

    # Inert to DRF, which reads no `Meta` off a plain `Serializer`, and the only thing a schema
    # generator has to go on: drf-spectacular types a read-only `PrimaryKeyRelatedField` from
    # `field.parent.Meta.model`, and without one it documents every relation here as a bare string
    # and warns while doing it. A class statement rather than `type()`, so the name is not a string
    # anything could get wrong unobserved.
    class Meta:
        model = event_model

    attrs["Meta"] = Meta

    return type(name or f"{model.__name__}HistorySerializer", (serializers.Serializer,), attrs)


def _context_fields(event_model, context_names):
    """{output name: Field} for every `ContextField` column on event_model, read from the `pgh_data`
    snapshot and typed after the column."""
    fields = {}
    for field in event_model._meta.fields:
        if field.name not in context_names:
            continue
        if isinstance(field, models.ForeignKey):
            output_name = f"{field.name}_id"
            field_cls = _FIELD_TYPES.get(type(field.target_field), serializers.CharField)
        else:
            output_name = field.name
            field_cls = _FIELD_TYPES.get(type(field), serializers.CharField)
        fields[output_name] = field_cls(source=f"pgh_data.{field.column}", read_only=True, allow_null=field.null)
    return fields
