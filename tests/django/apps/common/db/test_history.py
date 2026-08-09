import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from isik.django.apps.common.db.history import event_model_for, history_middleware_installed
from tests.testapp.models import Comment, Widget, WidgetEvent


pytestmark = pytest.mark.django_db


def test_track_events_registers_insert_update_delete_triggers():
    trackers = {trigger.name for trigger in Widget._meta.triggers}
    assert trackers == {"insert_insert", "update_update", "delete_delete"}


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


def test_event_model_for_raises_when_tracked_by_more_than_one_event_model(monkeypatch):
    monkeypatch.setattr(Widget, "pgh_event_models", {"insert": WidgetEvent, "custom": object})
    with pytest.raises(ImproperlyConfigured, match="more than one event model"):
        event_model_for(Widget)
