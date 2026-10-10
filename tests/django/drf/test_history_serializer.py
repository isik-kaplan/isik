from types import SimpleNamespace

import pghistory
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps
from pghistory.models import Events
from rest_framework import serializers

from isik.django.apps.common.db.history import event_model_for
from isik.django.drf.serializers.history import _context_fields, generic_history_serializer
from tests.testapp.models import Comment, ContextTrackedWidget, EmailUser, Widget


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="x")


def history_for(model, obj):
    return Events.objects.across(event_model_for(model)).tracks(obj).order_by("pgh_id")


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count", "owner", "created_at"]


class ContextTrackedWidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContextTrackedWidget
        fields = ["id", "name", "updated_at"]


def widget_history(serializer=WidgetSerializer, **kwargs):
    return generic_history_serializer(Widget, serializer, **kwargs)


def rendered(Serializer, obj, model=Widget):
    return Serializer(history_for(model, obj), many=True).data


def test_raises_on_an_untracked_model():
    with pytest.raises(ImproperlyConfigured, match="has no @track_events"):
        generic_history_serializer(Comment, WidgetSerializer)


class TestWhatItShows:
    """The fields of the resource's own serializer that render one tracked column - nothing else."""

    def test_an_entry_has_the_event_fields_then_the_serializers_fields(self, alice):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)

        (insert,) = rendered(widget_history(), widget)

        assert list(insert) == [
            "event_id",
            "event_created_at",
            "action",
            "changes",
            "id",
            "name",
            "count",
            "owner",
            "created_at",
        ]
        assert insert["action"] == "insert"
        assert insert["changes"] is None

    def test_each_value_renders_as_the_resource_renders_it(self, alice):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        widget.refresh_from_db()

        (insert,) = rendered(widget_history(), widget)

        resource = WidgetSerializer(widget).data
        assert {key: insert[key] for key in resource} == resource

    def test_a_null_relation_renders_as_none(self):
        widget = Widget.objects.create(name="bolt", count=1)

        assert rendered(widget_history(), widget)[0]["owner"] is None

    def test_a_renamed_field_keeps_the_serializers_name(self):
        class TitleSerializer(serializers.Serializer):
            title = serializers.CharField(source="name")

        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(name="nut")

        update = rendered(widget_history(TitleSerializer), widget)[1]
        assert update["title"] == "nut"
        assert update["changes"] == {"title": ["bolt", "nut"]}

    def test_a_relation_by_its_column_renders_its_primary_key(self, alice):
        class OwnerIdSerializer(serializers.Serializer):
            owner_id = serializers.IntegerField()

        widget = Widget.objects.create(name="bolt", count=1, owner=alice)

        assert rendered(widget_history(OwnerIdSerializer), widget)[0]["owner_id"] == alice.pk

    def test_fields_that_dont_render_one_column_are_left_out(self):
        class UserSerializer(serializers.ModelSerializer):
            class Meta:
                model = EmailUser
                fields = ["id", "username"]

        class EverythingSerializer(serializers.Serializer):
            count = serializers.IntegerField(write_only=True)
            computed = serializers.SerializerMethodField()
            name = serializers.CharField()
            whole = serializers.CharField(source="*")
            shouted = serializers.CharField(source="name.upper")
            label = serializers.CharField(source="__str__")
            owner_name = serializers.CharField(source="owner.username")
            owner_nested = UserSerializer(source="owner")
            owner_slug = serializers.SlugRelatedField(source="owner", slug_field="username", read_only=True)
            owner_text = serializers.CharField(source="owner")
            owner_id_related = serializers.PrimaryKeyRelatedField(source="owner_id", read_only=True)

        shown = set(widget_history(EverythingSerializer)().fields) - {
            "event_id",
            "event_created_at",
            "action",
            "changes",
        }

        assert shown == {"name"}

    def test_a_relation_by_its_column_renders_a_change_from_null(self, alice):
        class OwnerIdSerializer(serializers.Serializer):
            owner_id = serializers.IntegerField()

        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(owner=alice)

        assert rendered(widget_history(OwnerIdSerializer), widget)[1]["changes"] == {"owner_id": [None, alice.pk]}

    def test_a_field_built_with_positional_arguments_is_rebuilt_with_them(self):
        class ChoiceSerializer(serializers.Serializer):
            name = serializers.ChoiceField(["bolt", "nut"])

        widget = Widget.objects.create(name="bolt", count=1)

        assert widget_history(ChoiceSerializer)().fields["name"].choices == {"bolt": "bolt", "nut": "nut"}
        assert rendered(widget_history(ChoiceSerializer), widget)[0]["name"] == "bolt"

    def test_the_event_and_context_fields_are_read_only(self):
        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            widget_fields = widget_history()().fields
        context_fields = generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)().fields

        for name in ["event_id", "event_created_at", "action", "changes", "actor_id"]:
            assert widget_fields[name].read_only is True
        for name in ["actor_id", "actor_schema", "tenant_id"]:
            assert context_fields[name].read_only is True
            assert context_fields[name].allow_null is True

    def test_a_shown_field_is_the_serializers_own_field_type(self):
        fields = widget_history()().fields

        assert isinstance(fields["name"], serializers.CharField)
        assert isinstance(fields["owner"], serializers.PrimaryKeyRelatedField)
        assert type(fields["owner"]).__name__ == "PrimaryKeyRelatedField"

    def test_history_columns_are_the_shown_and_marked_columns(self):
        Serializer = widget_history(shows_change_of=["updated_at"])

        assert Serializer.history_columns == {"id", "name", "count", "owner_id", "created_at", "updated_at"}


class TestChanges:
    def test_lists_a_shown_fields_change_rendered_by_its_field(self, alice):
        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=5, owner=alice)

        update = rendered(widget_history(), widget)[1]

        assert update["changes"] == {"count": [1, 5], "owner": [None, alice.pk]}

    def test_leaves_out_a_hidden_change(self):
        class NameSerializer(serializers.Serializer):
            name = serializers.CharField()

        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(count=5)

        assert rendered(widget_history(NameSerializer), widget)[1]["changes"] is None

    def test_shows_change_of_lists_a_hidden_change_without_either_value(self, alice):
        class NameSerializer(serializers.Serializer):
            name = serializers.CharField()

        widget = Widget.objects.create(name="bolt", count=1)
        widget.update(owner=alice, name="nut")

        update = rendered(widget_history(NameSerializer, shows_change_of=["owner"]), widget)[1]

        assert update["changes"] == {"name": ["bolt", "nut"], "owner": [None, None]}
        assert "owner" not in update

    @pytest.mark.parametrize("field_name", ["nope", "pgh_label"])
    def test_shows_change_of_refuses_a_field_that_isnt_tracked(self, field_name):
        with pytest.raises(
            ImproperlyConfigured,
            match=rf"^shows_change_of names '{field_name}', which isn't a tracked field of Widget that "
            r"WidgetSerializer hides - list only fields the history would otherwise leave out\.$",
        ):
            widget_history(shows_change_of=[field_name])

    def test_shows_change_of_refuses_a_context_field(self):
        with pytest.raises(ImproperlyConfigured, match=r"^shows_change_of names 'actor'"):
            generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer, shows_change_of=["actor"])

    def test_shows_change_of_refuses_a_field_the_serializer_shows(self):
        with pytest.raises(ImproperlyConfigured, match=r"^shows_change_of names 'owner'"):
            widget_history(shows_change_of=["owner"])


class TestCollisions:
    def test_a_field_named_like_an_event_field_raises(self):
        class ActionSerializer(serializers.Serializer):
            action = serializers.CharField(source="name")

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^ActionSerializer has field\(s\) named \['action'\], which the history of Widget uses for "
            r"its own - rename them in the serializer, or give the history a serializer of its own\.$",
        ):
            widget_history(ActionSerializer)

    def test_a_field_named_like_a_context_field_raises(self):
        class SchemaSerializer(serializers.Serializer):
            actor_schema = serializers.CharField(source="name")

        with pytest.raises(ImproperlyConfigured, match=r"named \['actor_schema'\]"):
            generic_history_serializer(ContextTrackedWidget, SchemaSerializer)


@isolate_apps("tests.testapp")
def test_context_fields_use_charfield_as_the_fallback_for_an_unmapped_fk_target_type():
    class Weird(models.Model):
        id = models.PositiveBigIntegerField(primary_key=True)

        class Meta:
            app_label = "testapp"

    class WeirdEvent(models.Model):
        pgh_id = models.IntegerField()
        weird = models.ForeignKey(Weird, on_delete=models.CASCADE)
        other = models.IntegerField()

        class Meta:
            app_label = "testapp"

    fields = _context_fields(WeirdEvent, {"weird"})
    assert set(fields) == {"weird_id"}
    assert isinstance(fields["weird_id"], serializers.CharField)


@isolate_apps("tests.testapp")
def test_context_fields_are_typed_after_their_column():
    class TypedEvent(models.Model):
        pgh_id = models.IntegerField()
        level = models.IntegerField(null=True)

        class Meta:
            app_label = "testapp"

    field = _context_fields(TypedEvent, {"level"})["level"]
    assert isinstance(field, serializers.IntegerField)
    assert field.allow_null is True


@isolate_apps("tests.testapp")
def test_context_fields_use_charfield_as_the_fallback_for_an_unmapped_non_fk_type():
    class WeirdEvent(models.Model):
        pgh_id = models.IntegerField()
        weird_value = models.PositiveBigIntegerField()

        class Meta:
            app_label = "testapp"

    fields = _context_fields(WeirdEvent, {"weird_value"})
    assert isinstance(fields["weird_value"], serializers.CharField)
    assert fields["weird_value"].allow_null is False


def test_actor_id_is_absent_by_default():
    assert "actor_id" not in widget_history()().fields


def test_actor_id_is_present_and_nullable_when_history_middleware_is_installed():
    with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
        field = widget_history()().fields["actor_id"]
    assert isinstance(field, serializers.CharField)
    assert field.allow_null is True


def test_changes_allows_null():
    assert widget_history()().fields["changes"].allow_null is True


class TestContextFieldsAndActorIdCompose:
    """A ContextField producing actor_id (an FK context field named "actor") used to collide with
    generic_history_serializer()'s own reserved actor_id name - see
    isik/django/apps/common/db/history.py's pgh_context_field_names."""

    def test_builds_without_raising(self):
        generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)

    def test_actor_id_is_sourced_from_the_real_column_not_the_json_annotation(self):
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")

        with pghistory.context(user=alice.pk):
            widget = ContextTrackedWidget.objects.create(name="bolt")

        Serializer = generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)
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

        Serializer = generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data

        assert data[0]["actor_schema"] == "tenant_1"
        assert data[0]["tenant_id"] == org.pk

    def test_context_fields_are_null_outside_any_pghistory_context(self):
        widget = ContextTrackedWidget.objects.create(name="bolt")

        Serializer = generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)
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

        Serializer = generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data

        insert, update = data
        assert insert["changes"] is None
        assert update["changes"]["name"] == ["bolt", "nut"]
        assert "actor_id" not in update["changes"]

    def test_changes_still_reports_updated_at_when_only_the_actor_changed(self):
        # A pure actor handoff (no real field edit) still leaves updated_at in the diff - BaseModel
        # stamps it on every UPDATE - and the serializer shows it, so only actor_id is filtered out.
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")
        bob = EmailUser.objects.create(username="bob", email="bob@example.com")

        with pghistory.context(user=alice.pk):
            widget = ContextTrackedWidget.objects.create(name="bolt")
        with pghistory.context(user=bob.pk):
            widget.update(name="bolt")

        Serializer = generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)
        data = Serializer(history_for(ContextTrackedWidget, widget), many=True).data

        assert data[1]["changes"] == {"updated_at": [data[0]["updated_at"], data[1]["updated_at"]]}

    def test_a_context_actor_id_wins_over_the_middlewares_even_with_it_installed(self):
        with override_settings(MIDDLEWARE=["pghistory.middleware.HistoryMiddleware"]):
            Serializer = generic_history_serializer(ContextTrackedWidget, ContextTrackedWidgetSerializer)
        assert isinstance(Serializer().fields["actor_id"], serializers.IntegerField)


class TestAFeedOverSeveralEventModels:
    """A serializer is built for one model, and a feed selecting across several hands it rows that
    were recorded for another. Those rows carry no column of this model's, and the field reads one."""

    def _feed(self):
        return Events.objects.across(event_model_for(Widget), event_model_for(ContextTrackedWidget)).order_by("pgh_id")

    def test_a_row_recorded_for_another_model_renders_this_models_fields_as_null(self, alice):
        """Every one of them, not only the columns that happen to be absent. A `ContextTrackedWidget`
        row carries `id` and `name` under those same names, and they are a different object's - shown
        here they would read as this widget's own. `count` and `owner` it has not got at all, and
        reading one used to raise `KeyError` rather than render the absence."""
        Widget.objects.create(name="bolt", count=1, owner=alice)
        ContextTrackedWidget.objects.create(name="other")

        entries = widget_history()(self._feed(), many=True).data

        # A set because order across two streams is nobody's guarantee: one row is the widget's own,
        # the other has nothing of this model on it.
        assert {(entry["name"], entry["count"], entry["owner"]) for entry in entries} == {
            ("bolt", 1, alice.pk),
            (None, None, None),
        }

    def test_a_row_of_this_model_missing_the_column_is_not_quietly_absent(self):
        """A snapshot that should carry the column and does not means the two disagree about what is
        tracked. Rendering null there would hide it for as long as nobody compared the two."""
        field = widget_history()().fields["count"]
        recorded_here = SimpleNamespace(pgh_model=event_model_for(Widget)._meta.label, pgh_data={})

        with pytest.raises(KeyError):
            field.get_attribute(recorded_here)

    def test_a_row_with_no_pgh_model_at_all_is_read_as_this_models(self):
        """`pgh_model` is a column of the aggregate `Events` only. A queryset of one concrete event
        model has no such attribute, and every row in it is this field's to read."""
        field = widget_history()().fields["count"]
        concrete = SimpleNamespace(pgh_data={"count": 7})

        assert field.get_attribute(concrete) == 7

    def test_the_generated_class_names_its_event_model_for_a_schema_generator(self):
        """Inert to DRF, which reads no `Meta` off a plain `Serializer`. drf-spectacular types a
        read-only relation from `field.parent.Meta.model`, and documents it as a bare string without
        one - so the schema isik itself generates was wrong about every relation in a history."""
        assert widget_history().Meta.model is event_model_for(Widget)
