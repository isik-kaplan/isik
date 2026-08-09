import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from pghistory.models import Events

from isik.django.apps.common.db.history import event_model_for
from isik.django.drf.serializers.history import generic_history_serializer
from tests.testapp.models import Comment, Widget


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


def history_for(model, obj):
    return Events.objects.across(event_model_for(model)).tracks(obj).order_by("pgh_id")


def test_raises_on_an_untracked_model():
    with pytest.raises(ImproperlyConfigured, match="has no @track_events"):
        generic_history_serializer(Comment)


def test_meta_field_names_are_renamed_and_tracked_fields_are_flattened(alice):
    widget = Widget.objects.create(name="bolt", count=1, owner=alice)
    widget.update(count=5)

    WidgetHistorySerializer = generic_history_serializer(Widget)
    data = WidgetHistorySerializer(history_for(Widget, widget), many=True).data

    insert, update = data
    assert set(insert) == {
        "event_id",
        "event_created_at",
        "action",
        "changes",
        "name",
        "count",
        "owner_id",
        "id",
        "created_at",
        "updated_at",
    }
    assert insert["action"] == "insert"
    assert insert["changes"] is None
    assert insert["name"] == "bolt"
    assert insert["count"] == 1
    assert insert["owner_id"] == alice.pk
    assert insert["id"] == str(widget.pk)

    assert update["action"] == "update"
    assert update["changes"] == {"count": [1, 5]}
    assert update["count"] == 5


def test_actor_id_is_absent_by_default(alice):
    widget = Widget.objects.create(name="bolt", count=1)
    WidgetHistorySerializer = generic_history_serializer(Widget)
    data = WidgetHistorySerializer(history_for(Widget, widget), many=True).data
    assert "actor_id" not in data[0]


def test_actor_id_is_present_when_history_middleware_is_installed():
    with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
        WidgetHistorySerializer = generic_history_serializer(Widget)
        assert "actor_id" in WidgetHistorySerializer().fields
