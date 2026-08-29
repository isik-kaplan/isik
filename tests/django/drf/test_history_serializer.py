import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps
from pghistory.models import Events
from rest_framework import serializers

from isik.django.apps.common.db import track_events
from isik.django.apps.common.db.history import event_model_for
from isik.django.drf.serializers.history import _tracked_fields, generic_history_serializer
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


@isolate_apps("tests.testapp")
def test_raises_on_a_tracked_field_colliding_with_a_meta_field_name():
    # Both pghistory (stamps the generated Event class onto the real tests.testapp.models module)
    # and pgtrigger (a process-global trigger registry keyed by db_table) track a tracked model
    # by name outside isolate_apps' own registry - a fixed class name would collide with itself on
    # a second in-process run of this test (e.g. a mutation-testing tool re-invoking pytest
    # without restarting), so the model name has to be unique to this run instead.
    model_name = f"CollidingWidget{uuid.uuid4().hex[:8]}"
    attrs = {
        "action": models.CharField(max_length=10),
        "__module__": __name__,
        "Meta": type("Meta", (), {"app_label": "testapp"}),
    }
    CollidingWidget = track_events()(type(model_name, (models.Model,), attrs))
    with pytest.raises(
        ImproperlyConfigured,
        match=r"^.+ has tracked field\(s\) named \['action'\], which collide with "
        r"generic_history_serializer\(\)'s own field names - rename the model field or exclude it "
        r"from tracking \(track_events\(exclude=\[\.\.\.\]\)\)\.$",
    ):
        generic_history_serializer(CollidingWidget)


@isolate_apps("tests.testapp")
def test_tracked_fields_uses_charfield_as_the_fallback_for_an_unmapped_fk_target_type():
    class Weird(models.Model):
        id = models.PositiveBigIntegerField(primary_key=True)

        class Meta:
            app_label = "testapp"

    class WeirdEvent(models.Model):
        pgh_id = models.IntegerField()
        weird = models.ForeignKey(Weird, on_delete=models.CASCADE)

        class Meta:
            app_label = "testapp"

    fields = _tracked_fields(WeirdEvent)
    assert isinstance(fields["weird_id"], serializers.CharField)


@isolate_apps("tests.testapp")
def test_tracked_fields_uses_charfield_as_the_fallback_for_an_unmapped_non_fk_type():
    class WeirdEvent(models.Model):
        pgh_id = models.IntegerField()
        weird_value = models.PositiveBigIntegerField()

        class Meta:
            app_label = "testapp"

    fields = _tracked_fields(WeirdEvent)
    assert isinstance(fields["weird_value"], serializers.CharField)


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
    # updated_at moves too - BaseModel's own trigger stamps it on every UPDATE (see
    # isik/django/apps/common/db/models.py), including the widget.update() above.
    assert update["changes"]["count"] == [1, 5]
    assert "updated_at" in update["changes"]
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


def test_every_field_is_read_only():
    with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
        fields = generic_history_serializer(Widget)().fields
        assert all(field.read_only for field in fields.values())


def test_changes_and_actor_id_allow_null():
    with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
        fields = generic_history_serializer(Widget)().fields
        assert fields["changes"].allow_null is True
        assert fields["actor_id"].allow_null is True


def test_tracked_field_allow_null_reflects_the_real_model_field_nullability():
    fields = generic_history_serializer(Widget)().fields
    assert fields["name"].allow_null is False  # Widget.name has no null=True
    assert fields["owner_id"].allow_null is True  # Widget.owner has null=True
