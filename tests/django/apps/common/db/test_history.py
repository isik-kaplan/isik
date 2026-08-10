import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from isik.django.apps.common.db import track_events
from isik.django.apps.common.db.history import event_model_for, history_middleware_installed
from tests.testapp.models import Comment, Widget, WidgetEvent


pytestmark = pytest.mark.django_db


def test_track_events_registers_insert_update_delete_triggers():
    trackers = {trigger.name for trigger in Widget._meta.triggers}
    assert trackers == {"insert_insert", "update_update", "delete_delete"}


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
