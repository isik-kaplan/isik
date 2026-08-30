import uuid

import pghistory
import pgtrigger
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from isik.django.apps.common.db import history as history_module
from isik.django.apps.common.db import track_events
from isik.django.apps.common.db.history import ContextField, event_model_for, history_middleware_installed
from tests.testapp.models import Comment, ContextTrackedWidget, EmailUser, Widget, WidgetEvent


pytestmark = pytest.mark.django_db


def test_track_events_registers_insert_update_delete_triggers():
    # Widget also carries BaseModel's own created_at/updated_at triggers (see
    # isik/django/apps/common/db/models.py) - this only asserts @track_events()'s contribution.
    trackers = {trigger.name for trigger in Widget._meta.triggers}
    assert {"insert_insert", "update_update", "delete_delete"} <= trackers


@isolate_apps("tests.testapp")
def test_track_events_forwards_its_own_trackers_not_just_kwargs():
    # A fresh, uniquely-named model - both pghistory (stamps the generated Event class onto the
    # real tests.testapp.models module) and pgtrigger (a process-global trigger registry keyed by
    # db_table) track a tracked model by name outside isolate_apps' own registry, so a fixed class
    # name would collide with itself on a second in-process run of this test (e.g. a
    # mutation-testing tool re-invoking pytest without restarting).
    model_name = f"FreshTrackedWidget{uuid.uuid4().hex[:8]}"
    attrs = {
        "name": models.CharField(max_length=100),
        "__module__": __name__,
        "Meta": type("Meta", (), {"app_label": "testapp"}),
    }
    FreshTrackedWidget = track_events()(type(model_name, (models.Model,), attrs))

    trigger_names = {trigger.name for trigger in FreshTrackedWidget._meta.triggers}
    assert trigger_names == {"insert_insert", "update_update", "delete_delete"}


@isolate_apps("tests.testapp")
def test_track_events_forwards_its_own_kwargs_to_pghistory_track():
    model_name = f"FreshFieldRestrictedWidget{uuid.uuid4().hex[:8]}"
    attrs = {
        "name": models.CharField(max_length=100),
        "count": models.IntegerField(default=0),
        "__module__": __name__,
        "Meta": type("Meta", (), {"app_label": "testapp"}),
    }
    Tracked = track_events(fields=["name"])(type(model_name, (models.Model,), attrs))

    event_field_names = {f.name for f in event_model_for(Tracked)._meta.get_fields()}
    assert "name" in event_field_names
    assert "count" not in event_field_names


def test_track_events_creates_a_history_model():
    assert WidgetEvent._meta.app_label == "testapp"
    assert hasattr(WidgetEvent, "pgh_label")
    assert hasattr(WidgetEvent, "pgh_obj")


def test_history_is_recorded_end_to_end_via_the_real_postgres_trigger():
    widget = Widget.objects.create(name="bolt", count=3)
    widget_id = widget.id
    widget.update(count=5)
    widget.delete()

    events = list(WidgetEvent.objects.filter(pgh_obj_id=widget_id).order_by("pgh_id").values_list("pgh_label", "count"))
    assert events == [("insert", 3), ("update", 5), ("delete", 5)]


def test_event_model_for_returns_the_generated_event_model():
    assert event_model_for(Widget) is WidgetEvent


def test_event_model_for_raises_on_an_untracked_model():
    with pytest.raises(ImproperlyConfigured, match="has no @track_events"):
        event_model_for(Comment)


def test_history_middleware_installed_is_false_by_default():
    assert history_middleware_installed() is False


def test_history_middleware_installed_detects_the_middleware_itself():
    with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
        assert history_middleware_installed() is True


def test_history_middleware_installed_detects_a_subclass():
    with override_settings(MIDDLEWARE=["tests.testapp.middleware.CustomHistoryMiddleware"]):
        assert history_middleware_installed() is True


def test_history_middleware_installed_ignores_unrelated_middleware():
    with override_settings(MIDDLEWARE=["django.contrib.sessions.middleware.SessionMiddleware"]):
        assert history_middleware_installed() is False


def test_history_middleware_installed_skips_an_unimportable_middleware_entry():
    with override_settings(MIDDLEWARE=["not.a.real.module.Thing"]):
        assert history_middleware_installed() is False


def test_history_middleware_installed_continues_past_an_unimportable_entry_to_a_later_valid_one():
    # continue, not break - an earlier unimportable entry must not stop the loop from reaching a
    # real HistoryMiddleware listed after it.
    with override_settings(MIDDLEWARE=["not.a.real.module.Thing", "pghistory.middleware.HistoryMiddleware"]):
        assert history_middleware_installed() is True


def test_event_model_for_raises_when_tracked_by_more_than_one_event_model(monkeypatch):
    monkeypatch.setattr(Widget, "pgh_event_models", {"insert": WidgetEvent, "custom": object})
    with pytest.raises(ImproperlyConfigured, match="more than one event model"):
        event_model_for(Widget)


class TestContextField:
    def test_resolved_context_key_defaults_to_the_field_name(self):
        cf = ContextField("actor", field=models.IntegerField())
        assert cf.resolved_context_key() == "actor"

    def test_resolved_context_key_uses_context_key_when_given(self):
        cf = ContextField("actor", context_key="user", field=models.IntegerField())
        assert cf.resolved_context_key() == "user"

    def test_resolved_cast_uses_the_explicit_cast_when_given(self):
        cf = ContextField("actor", field=models.IntegerField(), cast="bigint")
        assert cf.resolved_cast() == "bigint"

    @pytest.mark.parametrize(
        ("field", "cast"),
        [
            (models.UUIDField(), "uuid"),
            (models.BooleanField(), "boolean"),
            (models.BigIntegerField(), "bigint"),
            (models.IntegerField(), "integer"),
            (models.CharField(max_length=10), "text"),
            (models.TextField(), "text"),
        ],
    )
    def test_resolved_cast_is_inferred_for_self_contained_field_types(self, field, cast):
        assert ContextField("f", field=field).resolved_cast() == cast

    def test_resolved_cast_raises_for_an_uninferrable_field_type_without_an_explicit_cast(self):
        cf = ContextField("f", field=models.JSONField())
        with pytest.raises(TypeError, match="needs cast="):
            cf.resolved_cast()

    def test_resolved_cast_raises_for_a_foreign_key_without_an_explicit_cast(self):
        # The related model may not be resolvable yet (this can run at import time, before
        # INSTALLED_APPS finishes loading) - ForeignKey is never inferred, cast= is required.
        cf = ContextField("actor", field=models.ForeignKey("testapp.EmailUser", on_delete=models.SET_NULL))
        with pytest.raises(TypeError, match="needs cast="):
            cf.resolved_cast()

    def test_column_uses_the_foreign_keys_id_suffix(self):
        cf = ContextField("actor", field=models.ForeignKey("testapp.EmailUser", on_delete=models.SET_NULL))
        assert cf.column() == "actor_id"

    def test_column_matches_the_field_name_for_a_plain_field(self):
        assert ContextField("actor", field=models.IntegerField()).column() == "actor"


def test_context_field_adds_a_real_column_to_the_generated_event_model():
    ContextTrackedWidgetEvent = event_model_for(ContextTrackedWidget)
    field = ContextTrackedWidgetEvent._meta.get_field("actor")
    assert isinstance(field, models.ForeignKey)
    assert field.db_index is True


def test_context_field_stamps_the_column_from_pghistory_context_on_insert_and_update():
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")

    with pghistory.context(user=alice.pk):
        widget = ContextTrackedWidget.objects.create(name="bolt")
        widget.update(name="nut")

    events = event_model_for(ContextTrackedWidget).objects.filter(pgh_obj_id=widget.pk).order_by("pgh_id")
    assert [e.actor_id for e in events] == [alice.pk, alice.pk]


def test_context_fields_composite_index_composes_via_track_events_meta_kwarg():
    # meta={"indexes": [...]} passed alongside context_fields= needs nothing isik-specific -
    # both just feed the same pghistory.track()/create_event_model() call.
    index_names = {index.name for index in event_model_for(ContextTrackedWidget)._meta.indexes}
    assert "ctx_widget_tenant_actor_idx" in index_names


def test_context_fields_all_stamp_from_one_trigger():
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")
    org = EmailUser.objects.create(username="org", email="org@example.com")

    with pghistory.context(user=alice.pk, schema="tenant_1", organization=org.pk):
        widget = ContextTrackedWidget.objects.create(name="bolt")

    event = event_model_for(ContextTrackedWidget).objects.get(pgh_obj_id=widget.pk, pgh_label="insert")
    assert (event.actor_id, event.actor_schema, event.tenant_id) == (alice.pk, "tenant_1", org.pk)


def test_context_field_is_null_outside_any_pghistory_context():
    widget = ContextTrackedWidget.objects.create(name="bolt")
    event = event_model_for(ContextTrackedWidget).objects.get(pgh_obj_id=widget.pk, pgh_label="insert")
    assert event.actor_id is None


def test_context_fields_attrs_and_trigger_returns_empty_for_no_fields():
    assert history_module._context_fields_attrs_and_trigger([]) == ({}, None)


def test_context_fields_attrs_and_trigger_builds_attrs_and_one_combined_trigger():
    actor_field = models.IntegerField(null=True)
    org_field = models.IntegerField(null=True)
    cf_actor = ContextField("actor", context_key="user", cast="bigint", field=actor_field)
    cf_org = ContextField("org_id", cast="integer", field=org_field)

    attrs, trigger = history_module._context_fields_attrs_and_trigger([cf_actor, cf_org])

    assert attrs == {
        "actor": actor_field,
        "org_id": org_field,
        "pgh_context_field_names": frozenset({"actor", "org_id"}),
    }
    assert trigger.name == "stamp_context_fields"
    assert trigger.when == pgtrigger.Before
    assert trigger.operation == pgtrigger.Insert
    assert trigger.func == (
        "NEW.\"actor\" = (NULLIF(current_setting('pghistory.context_metadata', true), '')"
        "::jsonb ->> 'user')::bigint;\n"
        "NEW.\"org_id\" = (NULLIF(current_setting('pghistory.context_metadata', true), '')"
        "::jsonb ->> 'org_id')::integer;\n"
        "RETURN NEW;"
    )


def test_context_fields_attrs_and_trigger_names_a_foreign_key_field_with_its_id_suffix():
    # A ForeignKey's real column is <name>_id, not <name> - the recorded name has to match, since
    # this is what generic_history_serializer()/HistoryMixin compare against their own field names.
    fk_field = models.ForeignKey("testapp.EmailUser", null=True, on_delete=models.DO_NOTHING, db_constraint=False)
    cf_actor = ContextField("actor", context_key="user", cast="bigint", field=fk_field)

    attrs, _ = history_module._context_fields_attrs_and_trigger([cf_actor])

    assert attrs["pgh_context_field_names"] == frozenset({"actor_id"})


def test_context_tracked_widget_event_records_its_context_field_names():
    ContextTrackedWidgetEvent = event_model_for(ContextTrackedWidget)
    assert ContextTrackedWidgetEvent.pgh_context_field_names == {"actor_id", "actor_schema", "tenant_id"}


@isolate_apps("tests.testapp")
def test_track_events_context_fields_merge_with_explicit_attrs_and_meta_kwargs():
    # Fresh, uniquely-named model - see test_track_events_forwards_its_own_trackers_not_just_kwargs
    # for why (pgtrigger's registry is process-global, keyed by db_table/trigger name).
    model_name = f"FreshMergedContextWidget{uuid.uuid4().hex[:8]}"
    attrs = {
        "name": models.CharField(max_length=100),
        "__module__": __name__,
        "Meta": type("Meta", (), {"app_label": "testapp"}),
    }
    extra_trigger = pgtrigger.Trigger(
        name="extra_marker", when=pgtrigger.Before, operation=pgtrigger.Insert, func="RETURN NEW;"
    )
    Tracked = track_events(
        context_fields=[ContextField("actor", cast="integer", field=models.IntegerField(null=True))],
        attrs={"extra_field": models.IntegerField(default=0)},
        meta={"triggers": [extra_trigger]},
    )(type(model_name, (models.Model,), attrs))

    event_fields = {f.name for f in event_model_for(Tracked)._meta.get_fields()}
    assert {"actor", "extra_field"} <= event_fields

    event_triggers = {t.name for t in event_model_for(Tracked)._meta.triggers}
    assert {"stamp_context_fields", "extra_marker"} <= event_triggers


@isolate_apps("tests.testapp")
def test_track_events_context_fields_combine_into_one_trigger_covering_every_field():
    # Fresh, uniquely-named model - see test_track_events_forwards_its_own_trackers_not_just_kwargs
    # for why (pgtrigger's registry is process-global, keyed by db_table/trigger name).
    model_name = f"FreshMultiContextWidget{uuid.uuid4().hex[:8]}"
    attrs = {
        "name": models.CharField(max_length=100),
        "__module__": __name__,
        "Meta": type("Meta", (), {"app_label": "testapp"}),
    }
    Tracked = track_events(
        context_fields=[
            ContextField("actor", context_key="user", cast="bigint", field=models.IntegerField(null=True)),
            ContextField("org_id", cast="integer", field=models.IntegerField(null=True)),
        ]
    )(type(model_name, (models.Model,), attrs))

    # stamp_context_fields lives on the event model (it writes into that table), unlike
    # insert_insert/update_update/delete_delete which live on the tracked model itself.
    event_triggers = event_model_for(Tracked)._meta.triggers
    stamp_trigger = next(t for t in event_triggers if t.name == "stamp_context_fields")
    assert stamp_trigger.func.count("NEW.") == 2
