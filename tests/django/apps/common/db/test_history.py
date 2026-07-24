import pytest

from tests.testapp.models import Widget, WidgetEvent


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
