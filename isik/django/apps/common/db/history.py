import copy
from dataclasses import dataclass

import pghistory
import pgtrigger
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.module_loading import import_string
from pghistory.core import DeleteEvent, InsertEvent, UpdateEvent
from pghistory.middleware import HistoryMiddleware


_INFERRED_CASTS = {
    models.UUIDField: "uuid",
    models.BooleanField: "boolean",
    models.BigIntegerField: "bigint",
    models.BigAutoField: "bigint",
    models.SmallIntegerField: "integer",
    models.IntegerField: "integer",
    models.AutoField: "integer",
    models.SlugField: "text",
    models.TextField: "text",
    models.CharField: "text",
}


@dataclass(frozen=True)
class ContextField:
    """
    One concrete, indexed column on a tracked model's event table, stamped from pghistory's own
    request-scoped context (`pghistory.context()`/`HistoryMiddleware`) at insert time - a real,
    indexable column instead of a `pgh_context__<key>` JSON lookup done at query time. Pass one
    or more to `track_events(context_fields=[...])`.

        track_events(
            context_fields=[
                ContextField("actor", context_key="user", cast="uuid", field=models.ForeignKey(
                    settings.AUTH_USER_MODEL, null=True, on_delete=models.DO_NOTHING, db_constraint=False,
                )),
                # A multi-tenant setup: "tenant" lives only in the shared public schema, unlike
                # "actor" above which is local to whichever tenant schema this migrates into.
                ContextField("tenant", context_key="organization", cast="uuid", field=models.ForeignKey(
                    "organizations.Organization", null=True, on_delete=models.DO_NOTHING, db_constraint=False,
                )),
            ],
            # Composes for free - meta= and context_fields= both just feed the same
            # pghistory.track()/create_event_model() call, nothing isik-specific about it.
            meta={"indexes": [models.Index(fields=["tenant", "actor"], name="widget_event_tenant_actor_idx")]},
        )

    `name` is the field's attribute name on the event model, same as any other model field -
    indexing needs nothing isik-specific: a plain `db_index=True` on the field for a single-column
    index (skip it if a `Meta.indexes` entry below already covers that field as its leftmost
    column - redundant otherwise), or `meta={"indexes": [...]}` above for a composite one.

    `on_delete=models.DO_NOTHING, db_constraint=False` (mirroring pghistory's own default for
    `pgh_obj`/`pgh_context`) is usually what you want on a `ForeignKey` here: an event row is a
    historical record, and it shouldn't vanish or block a delete just because the actor/tenant it
    once pointed to is gone.

    `context_key` is the key read out of whatever `pghistory.context(**metadata)`/
    `HistoryMiddleware.get_context()` attached - defaults to `name`.

    `cast` is the Postgres type the extracted JSON value is cast to before assignment. Inferred
    for a handful of self-contained field types (UUID/int/bigint/text/bool); required for
    `ForeignKey` - the related model may not be loaded yet when this runs (this can execute at
    import time, before `INSTALLED_APPS` has finished loading), so it can't be resolved for you -
    and for anything else outside that short list.
    """

    name: str
    field: models.Field
    context_key: str | None = None
    cast: str | None = None

    def resolved_context_key(self):
        return self.context_key or self.name

    def resolved_cast(self):
        if self.cast:
            return self.cast
        for field_type, cast in _INFERRED_CASTS.items():
            if isinstance(self.field, field_type):
                return cast
        raise TypeError(
            f"ContextField({self.name!r}) needs cast= - can't infer a Postgres type for "
            f"{self.field!r} (only {', '.join(t.__name__ for t in _INFERRED_CASTS)} are inferred; "
            "ForeignKey always needs an explicit cast)."
        )

    def column(self):
        # A copy - set_attributes_from_name() is also what Field.contribute_to_class() calls
        # when this same field instance is actually attached to the event model (in
        # _context_fields_attrs_and_trigger() below), and doing it here first, just to read the
        # resulting column name for the trigger SQL, must not disturb that later, real attach.
        probe = copy.deepcopy(self.field)
        probe.set_attributes_from_name(self.name)
        return probe.column


def _context_fields_attrs_and_trigger(context_fields):
    """
    `context_fields` -> (the event model's `attrs` for those fields, the one trigger that stamps
    all of them). Combined into a single `BEFORE INSERT` trigger rather than one per field - one
    trigger firing is cheaper than several, and there's no ordering to get wrong between them.

    `current_setting('pghistory.context_metadata', true)` is the Postgres session variable
    `pghistory.context()`/`HistoryMiddleware` already populate before every statement (also what
    `pghistory.ContextJSONField` denormalization reads) - not documented public API, but reading
    it needs no separate middleware of our own.
    """
    if not context_fields:
        return {}, None

    attrs = {cf.name: cf.field for cf in context_fields}
    # Recorded on the generated event model so generic_history_serializer()/HistoryMixin can tell
    # when one of these real columns already covers a name they'd otherwise reserve for themselves
    # (currently just actor_id) - a real, indexed column and their own JSON-derived fallback are the
    # same fact, and the real one should win rather than collide with or get shadowed by it.
    attrs["pgh_context_field_names"] = frozenset(
        f"{cf.name}_id" if isinstance(cf.field, models.ForeignKey) else cf.name for cf in context_fields
    )
    # The ContextField instances themselves, not just their resolved names - HistoryMixin's
    # context_field_filter() needs the original (unattached) Field back to build a filter over it.
    attrs["pgh_context_fields"] = tuple(context_fields)
    assignments = "\n".join(
        f"NEW.\"{cf.column()}\" = (NULLIF(current_setting('pghistory.context_metadata', true), '')"
        f"::jsonb ->> '{cf.resolved_context_key()}')::{cf.resolved_cast()};"
        for cf in context_fields
    )
    trigger = pgtrigger.Trigger(
        name="stamp_context_fields",
        when=pgtrigger.Before,
        operation=pgtrigger.Insert,
        func=f"{assignments}\nRETURN NEW;",
    )
    return attrs, trigger


def track_events(*, context_fields=(), **kwargs):
    def track_model_history(cls):
        """
        Instead of using pghistory.track() directly, if we need base configuration we will do it here.
        """
        trackers = [InsertEvent(), UpdateEvent(), DeleteEvent()]
        attrs, trigger = _context_fields_attrs_and_trigger(context_fields)
        if attrs:
            kwargs["attrs"] = {**attrs, **kwargs.get("attrs", {})}
        if trigger:
            meta = kwargs.get("meta", {})
            kwargs["meta"] = {**meta, "triggers": [*meta.get("triggers", []), trigger]}
        return pghistory.track(*trackers, **kwargs)(cls)

    return track_model_history


def event_model_for(model):
    """
    The Event model django-pghistory generated for `model` via `@track_events()`.

        event_model_for(Widget).objects.filter(pgh_obj_id=widget.pk)

    Raises ImproperlyConfigured if `model` isn't tracked, or is tracked under more than one Event
    model (`pghistory.track()` applied more than once with different labels).
    """
    if "pgh_event_model" not in dir(model):
        raise ImproperlyConfigured(f"{model.__name__} has no @track_events() history to serve.")
    try:
        return model.pgh_event_model
    except ValueError as e:
        raise ImproperlyConfigured(str(e)) from e


def history_middleware_installed():
    """
    True if `pghistory.middleware.HistoryMiddleware` (or a subclass) is in `settings.MIDDLEWARE` -
    that's what stamps `user`/`url` into pghistory's context, so tracked events carry an actor.
    """
    for path in settings.MIDDLEWARE:
        try:
            middleware_cls = import_string(path)
        except ImportError:
            continue
        if isinstance(middleware_cls, type) and issubclass(middleware_cls, HistoryMiddleware):
            return True
    return False
