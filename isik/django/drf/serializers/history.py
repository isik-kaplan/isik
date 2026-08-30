"""generic_history_serializer() - see its own docstring."""

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rest_framework import serializers

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
# id/created_at among the tracked fields below, and those have to win: they're the real object's
# identity/timestamp at that point in history, not metadata about the history record itself.
_META_FIELD_NAMES = {"event_id", "event_created_at", "action", "changes", "actor_id"}


class _ChangesField(serializers.JSONField):
    """`pgh_diff`, minus any keys a `ContextField` put there - see generic_history_serializer()'s
    own docstring for why those aren't a real change to the object."""

    def __init__(self, *, context_field_names, **kwargs):
        self._context_field_names = context_field_names
        super().__init__(**kwargs)

    def to_representation(self, value):
        diff = super().to_representation(value)
        filtered = {key: change for key, change in diff.items() if key not in self._context_field_names}
        return filtered or None


def generic_history_serializer(model):
    """
    Builds a read-only `Serializer` for the history of a model tracked with `@track_events()` -
    `event_id`, `event_created_at`, `action` ("insert"/"update"/"delete"), `changes` (a dict of
    `{field: [old, new]}` for whatever changed since the previous event of the same object, `None`
    on insert), plus every tracked field flattened at the top level under its own name, typed to
    match the real model field. A foreign key surfaces as `<field>_id` - the raw stored id, not a
    hydrated relation, since this reads from a JSON snapshot rather than a live queryset. Adds
    `actor_id` too, if `pghistory.middleware.HistoryMiddleware` (or a subclass) is installed - see
    `history_middleware_installed()`.

        WidgetHistorySerializer = generic_history_serializer(Widget)
        WidgetHistorySerializer(some_queryset, many=True).data

    Built off `pghistory.models.Events` (its cross-table aggregate, here scoped to just this one
    model's Event table) rather than the concrete `<Model>Event` model directly - that's what
    computes `changes` in SQL, comparing each event to the previous one of the same object.

    `changes` never includes a `ContextField` (`track_events(context_fields=[...])`) - pghistory
    computes the diff generically over every non-`pgh_`-prefixed column on the event row, so a
    `ContextField`'s own column would otherwise show up as a "change" whenever the acting context
    differs from the previous event (e.g. `{"actor_id": [alice.pk, bob.pk]}`), even though nothing
    about the tracked object itself changed - it's who acted, not what changed.

    Raises `ImproperlyConfigured` if a tracked field is itself named `event_id`/`event_created_at`/
    `action`/`changes`/`actor_id` - rather than silently letting one clobber the other. Exception:
    an `actor_id` produced by a `ContextField` (see `track_events(context_fields=[...])`) isn't a
    collision - it's the same fact `actor_id` would otherwise annotate from JSON, just as a real,
    typed column, so it wins instead of raising.
    """
    event_model = event_model_for(model)
    tracked = _tracked_fields(event_model)
    context_field_names = getattr(event_model, "pgh_context_field_names", frozenset())
    collisions = (_META_FIELD_NAMES & tracked.keys()) - context_field_names
    if collisions:
        raise ImproperlyConfigured(
            f"{model.__name__} has tracked field(s) named {sorted(collisions)}, which collide with "
            "generic_history_serializer()'s own field names - rename the model field or exclude it "
            "from tracking (track_events(exclude=[...]))."
        )

    attrs = {
        "event_id": serializers.IntegerField(source="pgh_id", read_only=True),
        "event_created_at": serializers.DateTimeField(source="pgh_created_at", read_only=True),
        "action": serializers.CharField(source="pgh_label", read_only=True),
        "changes": _ChangesField(
            source="pgh_diff", read_only=True, allow_null=True, context_field_names=context_field_names
        ),
        **tracked,
    }
    if history_middleware_installed() and "actor_id" not in tracked:
        # A queryset-level annotation (see HistoryMixin._history_base_queryset) rather than sourced
        # off pgh_context directly - pgh_context is null for any event that wasn't created inside a
        # request (a migration, a shell, a background job), and a plain `source="pgh_context.user"`
        # would crash DRF's attribute traversal on that None instead of quietly serializing null.
        # Skipped when tracked already has a real actor_id column (a ContextField) - that's typed
        # and indexed, this JSON fallback is neither.
        attrs["actor_id"] = serializers.CharField(read_only=True, allow_null=True)

    return type(f"{model.__name__}HistorySerializer", (serializers.Serializer,), attrs)


def _tracked_fields(event_model):
    """{field_name: Field} for every tracked (non pgh_*) column on event_model, read from the
    `pgh_data` JSON snapshot rather than the column directly."""
    fields = {}
    for field in event_model._meta.fields:
        if field.name.startswith("pgh_"):
            continue
        if isinstance(field, models.ForeignKey):
            output_name = f"{field.name}_id"
            field_cls = _FIELD_TYPES.get(type(field.target_field), serializers.CharField)
        else:
            output_name = field.name
            field_cls = _FIELD_TYPES.get(type(field), serializers.CharField)
        fields[output_name] = field_cls(source=f"pgh_data.{field.column}", read_only=True, allow_null=field.null)
    return fields
