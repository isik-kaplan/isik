import uuid

import pghistory
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps
from pghistory.models import Events
from rest_framework import serializers

from isik.django.apps.common.db import track_events
from isik.django.apps.common.db.history import event_model_for
from isik.django.drf.serializers.history import _ChangesField, _tracked_fields, generic_history_serializer
from tests.testapp.models import Comment, ContextTrackedWidget, EmailUser, Widget


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


class TestChangesField:
    def test_drops_keys_named_in_context_field_names(self):
        field = _ChangesField(context_field_names={"actor_id"})
        assert field.to_representation({"actor_id": [1, 2], "name": ["a", "b"]}) == {"name": ["a", "b"]}

    def test_returns_none_when_filtering_leaves_nothing(self):
        field = _ChangesField(context_field_names={"actor_id"})
        assert field.to_representation({"actor_id": [1, 2]}) is None

    def test_is_a_noop_with_no_context_field_names(self):
        field = _ChangesField(context_field_names=frozenset())
        assert field.to_representation({"name": ["a", "b"]}) == {"name": ["a", "b"]}


class TestContextFieldsAndActorIdCompose:
    """A ContextField producing actor_id (an FK context field named "actor") used to collide with
    generic_history_serializer()'s own reserved actor_id name - see
    isik/django/apps/common/db/history.py's pgh_context_field_names."""

    def test_builds_without_raising(self):
        generic_history_serializer(ContextTrackedWidget)

    def test_actor_id_is_sourced_from_the_real_column_not_the_json_annotation(self):
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")

        with pghistory.context(user=alice.pk):
            widget = ContextTrackedWidget.objects.create(name="bolt")

        Serializer = generic_history_serializer(ContextTrackedWidget)
        # actor_id is typed off the real FK column (an IntegerField), not the CharField the JSON
        # annotation would otherwise use - a plain int, not "<alice.pk>" as a string.
        assert isinstance(Serializer().fields["actor_id"], serializers.IntegerField)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data
        assert data[0]["actor_id"] == alice.pk

    def test_other_context_fields_serialize_from_their_own_columns(self):
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")
        org = EmailUser.objects.create(username="org", email="org@example.com")

        with pghistory.context(user=alice.pk, schema="tenant_1", organization=org.pk):
            widget = ContextTrackedWidget.objects.create(name="bolt")

        Serializer = generic_history_serializer(ContextTrackedWidget)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data

        assert data[0]["actor_schema"] == "tenant_1"
        assert data[0]["tenant_id"] == org.pk

    def test_context_fields_are_null_outside_any_pghistory_context(self):
        widget = ContextTrackedWidget.objects.create(name="bolt")

        Serializer = generic_history_serializer(ContextTrackedWidget)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data

        assert data[0]["actor_id"] is None
        assert data[0]["actor_schema"] is None
        assert data[0]["tenant_id"] is None

    def test_changes_excludes_context_fields_even_when_the_actor_changes(self):
        # pghistory computes the diff generically over every non-pgh_ column on the event row, so
        # actor_id would otherwise show up as a "change" on a handoff between two actors, even
        # though nothing about the tracked object itself changed - see _ChangesField.
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")
        bob = EmailUser.objects.create(username="bob", email="bob@example.com")

        with pghistory.context(user=alice.pk):
            widget = ContextTrackedWidget.objects.create(name="bolt")
        with pghistory.context(user=bob.pk):
            widget.update(name="nut")

        Serializer = generic_history_serializer(ContextTrackedWidget)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data

        insert, update = data
        assert insert["changes"] is None
        assert update["changes"]["name"] == ["bolt", "nut"]
        assert "actor_id" not in update["changes"]

    def test_changes_still_reports_updated_at_when_only_the_actor_changed(self):
        # A pure actor handoff (no real field edit) still leaves updated_at in the diff - BaseModel
        # stamps it on every UPDATE - so this never actually empties out to None in this codebase,
        # but it confirms filtering removes exactly actor_id and nothing else.
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")
        bob = EmailUser.objects.create(username="bob", email="bob@example.com")

        with pghistory.context(user=alice.pk):
            widget = ContextTrackedWidget.objects.create(name="bolt")
        with pghistory.context(user=bob.pk):
            widget.update(name="bolt")

        Serializer = generic_history_serializer(ContextTrackedWidget)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data

        assert data[1]["changes"].keys() == {"updated_at"}
