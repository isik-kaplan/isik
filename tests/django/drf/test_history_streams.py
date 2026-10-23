"""A history built from several streams - an object's own events and those of the models declared as part of it."""

import datetime

import pytest
from django.core.exceptions import ImproperlyConfigured
from pghistory.models import Events
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

from isik.django.drf.viewsets import ViewSetRegistryExemption
from isik.django.drf.viewsets import history as history_module
from isik.django.drf.viewsets.history import HistoryMixin, HistoryStream, _pk_filter_class, history_part_problems
from tests.django.drf.support import UnguardedModelViewSet
from tests.testapp.models import Box, BoxItem, Shelf, ShelfItem, ShelfLabel, Site, Tag, Widget


pytestmark = pytest.mark.django_db


class ShelfSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shelf
        fields = ["id", "name"]


class ShelfItemSerializer(serializers.ModelSerializer):
    """What an item's events show - its title, never its secret."""

    class Meta:
        model = ShelfItem
        fields = ["title"]


class ShelfViewSet(HistoryMixin, UnguardedModelViewSet):
    model = Shelf
    endpoint = "shelves"
    serializer_class = ShelfSerializer
    history_also = {
        "items": HistoryStream(ShelfItem, path="shelf", serializer=ShelfItemSerializer),
        "labels": HistoryStream(ShelfLabel, path="shelf"),
    }
    history_detail = {"labels": ("tag", "label")}
    history_list_scoped_to_queryset = True

    def get_queryset(self):
        # Shelves named "hidden" are no caller's to see.
        return Shelf.objects.exclude(name="hidden")


class BoxSerializer(serializers.ModelSerializer):
    class Meta:
        model = Box
        fields = ["id", "name"]


class BoxViewSet(HistoryMixin, UnguardedModelViewSet):
    model = Box
    endpoint = "boxes"
    serializer_class = BoxSerializer
    history_also = {"items": HistoryStream(BoxItem, path="box")}


@pytest.fixture(autouse=True)
def fresh_caches():
    for attribute in ("_history_filterset_class", "_history_serializer_classes"):
        if attribute in ShelfViewSet.__dict__:
            delattr(ShelfViewSet, attribute)


def history(shelf, **query):
    request = APIRequestFactory().get(f"/shelves/{shelf.pk}/history/", query)
    return ShelfViewSet.as_view({"get": "history"})(request, pk=shelf.pk)


def history_list(user, **query):
    request = APIRequestFactory().get("/shelves/history/", query)
    request.user = user
    return ShelfViewSet.as_view({"get": "history_list"})(request)


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(username="root", email="root@example.com", password="x")


@pytest.fixture
def shelf():
    return Shelf.objects.create(name="kitchen")


@pytest.fixture
def tag():
    return Tag.objects.create(label="fragile")


def rows(response):
    return response.data


def in_order(*models):
    """Gives each model's events a later time than the one before - one test's rows otherwise share
    one, the start of its transaction."""
    from django.utils import timezone

    from isik.django.apps.common.db import event_model_for

    start = timezone.now()
    for minutes, model in enumerate(models):
        event_model_for(model).objects.update(pgh_created_at=start + datetime.timedelta(minutes=minutes))


class TestAnObjectsHistory:
    def test_takes_in_each_declared_stream_newest_first(self, shelf, tag):
        ShelfItem.objects.create(shelf=shelf, name="cup", title="Cup")
        ShelfLabel.objects.create(shelf=shelf, tag=tag)
        in_order(Shelf, ShelfItem, ShelfLabel)

        got = rows(history(shelf))

        assert [row["part"] for row in got] == ["labels", "items", "shelf"]
        assert [row["action"] for row in got] == ["insert", "insert", "insert"]

    def test_every_row_is_about_the_owner(self, shelf, tag):
        ShelfItem.objects.create(shelf=shelf, name="cup")
        ShelfLabel.objects.create(shelf=shelf, tag=tag)

        got = rows(history(shelf))

        assert {row["subject_id"] for row in got} == {str(shelf.pk)}
        assert {row["subject_label"] for row in got} == {"shelf kitchen"}

    def test_a_part_rows_own_fields_are_the_owners_and_null(self, shelf):
        ShelfItem.objects.create(shelf=shelf, name="cup", title="Cup")
        in_order(Shelf, ShelfItem)

        item_row = rows(history(shelf))[0]

        assert (item_row["id"], item_row["name"]) == (None, None)

    def test_a_part_shows_only_what_its_serializer_shows(self, shelf):
        item = ShelfItem.objects.create(shelf=shelf, name="cup", title="Cup", secret="s1")
        ShelfItem.objects.filter(pk=item.pk).update(title="Mug", secret="s2", name="mug")

        update = next(row for row in rows(history(shelf)) if row["part"] == "items" and row["action"] == "update")

        # Its title, under its serializer's name - never its secret, nor its `name` passed off as the shelf's.
        assert update["changes"] == {"title": ["Cup", "Mug"]}
        assert "s2" not in str(update)

    def test_a_part_without_a_serializer_shows_none_of_its_columns(self, shelf, tag):
        label = ShelfLabel.objects.create(shelf=shelf, tag=tag)
        ShelfLabel.objects.filter(pk=label.pk).update(tag=Tag.objects.create(label="heavy"))

        update = next(row for row in rows(history(shelf)) if row["part"] == "labels" and row["action"] == "update")

        assert update["changes"] is None

    def test_a_part_update_changing_only_what_it_hides_isnt_listed(self, shelf):
        item = ShelfItem.objects.create(shelf=shelf, name="cup")
        ShelfItem.objects.filter(pk=item.pk).update(secret="s1")

        assert [row["action"] for row in rows(history(shelf)) if row["part"] == "items"] == ["insert"]

    def test_the_owners_own_changes_read_through_its_own_serializer(self, shelf):
        Shelf.objects.filter(pk=shelf.pk).update(name="pantry")

        update = rows(history(shelf))[0]

        assert (update["part"], update["changes"]) == ("shelf", {"name": ["kitchen", "pantry"]})

    def test_a_detail_names_what_a_part_row_points_at(self, shelf, tag):
        ShelfLabel.objects.create(shelf=shelf, tag=tag)
        in_order(Shelf, ShelfLabel)

        got = rows(history(shelf))

        assert [row["detail"] for row in got] == ["fragile", None]

    def test_a_subject_the_caller_cant_see_stays_unnamed(self):
        hidden = Shelf.objects.create(name="hidden")
        request = APIRequestFactory().get("/")
        view = ShelfViewSet(request=request, format_kwarg=None, kwargs={}, action="history")
        events = list(view.get_history_queryset(hidden))

        assert view.history_page_context(events)["subject_labels"] == {}


class TestScoping:
    def test_a_child_shelfs_events_arent_its_parents(self, shelf):
        child = Shelf.objects.create(name="drawer", parent=shelf)
        ShelfItem.objects.create(shelf=child, name="spoon")

        got = rows(history(shelf))

        assert [row["part"] for row in got] == ["shelf"]
        assert ShelfViewSet._needs_exact_scope() is True

    def test_a_label_pointing_at_it_by_another_key_isnt_its(self, shelf, tag):
        other = Shelf.objects.create(name="garage")
        ShelfLabel.objects.create(shelf=other, moved_from=shelf, tag=tag)

        assert [row["part"] for row in rows(history(shelf))] == ["shelf"]

    def test_without_a_second_key_references_alone_is_exact(self):
        box = Box.objects.create(name="a")
        other = Box.objects.create(name="b")
        BoxItem.objects.create(box=box, name="x")
        BoxItem.objects.create(box=other, name="y")
        request = APIRequestFactory().get("/")
        view = BoxViewSet(request=request, format_kwarg=None, kwargs={}, action="history")

        got = list(view.get_history_queryset(box))

        assert BoxViewSet._needs_exact_scope() is False
        assert len(got) == 2
        assert {view.history_serializer_class.subject_of(row) for row in got} == {str(box.pk)}

    def test_a_shelf_points_at_shelves_twice_so_its_scope_is_exact(self):
        assert ShelfViewSet._needs_exact_scope() is True


class TestFilters:
    def test_part_narrows_to_one_stream(self, shelf, tag):
        ShelfItem.objects.create(shelf=shelf, name="cup")
        ShelfLabel.objects.create(shelf=shelf, tag=tag)

        assert [row["part"] for row in rows(history(shelf, part="items"))] == ["items"]

    def test_object_id_is_the_object_a_row_is_about(self, shelf, superuser):
        other = Shelf.objects.create(name="garage")
        ShelfItem.objects.create(shelf=shelf, name="cup")
        ShelfItem.objects.create(shelf=other, name="saw")

        got = rows(history_list(superuser, object_id=str(shelf.pk)))

        assert {row["subject_id"] for row in got} == {str(shelf.pk)}
        assert {row["part"] for row in got} == {"shelf", "items"}

    def test_object_id_not_a_key_is_refused(self, superuser):
        response = history_list(superuser, object_id="not-a-uuid")

        assert response.status_code == 400
        assert response.data == {"object_id": ["Not a valid key."]}


class TestTheCrossObjectList:
    def test_scoped_to_the_queryset_leaves_out_what_it_hides(self, shelf, superuser):
        hidden = Shelf.objects.create(name="hidden")
        ShelfItem.objects.create(shelf=hidden, name="key")

        got = rows(history_list(superuser))

        assert {row["subject_id"] for row in got} == {str(shelf.pk)}

    def test_ordered_by_time_across_streams(self, shelf):
        view = ShelfViewSet()
        view.request = None

        assert view._history_order(Events.objects.none()).query.order_by == ("-pgh_created_at", "-pgh_id")


class TestTheHooks:
    def test_the_page_context_can_be_extended(self, shelf):
        class ExtendedShelfViewSet(ShelfViewSet):
            exempt_from_registry = ViewSetRegistryExemption(
                reason="a test's own variant of the shelf viewset, never routed"
            )

            def history_page_context(self, rows):
                return {**super().history_page_context(rows), "seen": len(rows)}

        request = APIRequestFactory().get("/")
        view = ExtendedShelfViewSet(request=request, format_kwarg=None, kwargs={}, action="history")

        assert view.history_page_context(list(view.get_history_queryset(shelf)))["seen"] == 1

    def test_a_single_streams_page_context_is_empty(self):
        from tests.django.drf.test_history_viewset import WidgetViewSet

        assert WidgetViewSet().history_page_context([]) == {}

    def test_the_serializer_is_built_once_per_cache_key(self, monkeypatch):
        first = ShelfViewSet.history_serializer_class
        assert ShelfViewSet.history_serializer_class is first

        monkeypatch.setattr(ShelfViewSet, "history_serializer_cache_key", classmethod(lambda cls: "other"))
        other = ShelfViewSet.history_serializer_class

        assert other is not first
        assert ShelfViewSet.history_serializer_class is other


class TestTheActorFilter:
    def test_follows_an_integer_user_pk(self):
        from tests.testapp.models import EmailUser

        assert _pk_filter_class(EmailUser).__name__ == "_JSONContextNumberFilter"

    def test_follows_a_uuid_pk(self):
        assert _pk_filter_class(Widget).__name__ == "_JSONContextUUIDFilter"


def shelf_view(history_also=None, history_detail=None):
    attrs = {
        "exempt_from_registry": ViewSetRegistryExemption(
            reason="a test's own variant of the shelf viewset, never routed"
        ),
        "history_also": history_also if history_also is not None else {},
        "history_detail": history_detail if history_detail is not None else {},
    }
    return type("WrongShelfViewSet", (ShelfViewSet,), attrs)


class TestTheDeclarations:
    def test_the_routed_ones_are_fine(self):
        assert history_part_problems(ShelfViewSet) == []
        assert history_part_problems(BoxViewSet) == []

    def test_each_wrong_stream_is_named(self):
        view = shelf_view(
            {
                "shelf": HistoryStream(ShelfItem, path="shelf"),
                "untracked": HistoryStream(Site, path="name"),
                "not_a_key": HistoryStream(ShelfItem, path="name"),
                "elsewhere": HistoryStream(ShelfLabel, path="tag"),
                "nowhere": HistoryStream(ShelfLabel, path="missing"),
                "plain": "items",
            }
        )

        assert history_part_problems(view) == [
            "WrongShelfViewSet.history_also['shelf'] takes the key of testapp.Shelf's own stream - name it otherwise.",
            "WrongShelfViewSet.history_also['untracked']: testapp.Site isn't tracked - give it @track_events().",
            "WrongShelfViewSet.history_also['not_a_key']: 'name' isn't a foreign key or one-to-one of "
            "testapp.ShelfItem to testapp.Shelf.",
            "WrongShelfViewSet.history_also['elsewhere']: 'tag' isn't a foreign key or one-to-one of "
            "testapp.ShelfLabel to testapp.Shelf.",
            "WrongShelfViewSet.history_also['nowhere']: 'missing' isn't a foreign key or one-to-one of "
            "testapp.ShelfLabel to testapp.Shelf.",
            "WrongShelfViewSet.history_also['plain'] is 'items', not a HistoryStream.",
        ]

    def test_a_path_its_history_doesnt_record(self, monkeypatch):
        from isik.django.apps.common.db import event_model_for

        monkeypatch.setattr(history_module, "event_model_for", lambda model: event_model_for(Box))
        view = shelf_view({"items": HistoryStream(ShelfItem, path="shelf")})

        assert history_part_problems(view) == [
            "WrongShelfViewSet.history_also['items']: testapp.BoxEvent doesn't record 'shelf' - track it in the "
            "history."
        ]

    def test_each_wrong_detail_is_named(self):
        view = shelf_view(
            {"labels": HistoryStream(ShelfLabel, path="shelf"), "more": HistoryStream(ShelfLabel, path="shelf")},
            {
                "missing": ("tag", "label"),
                "plain": ("tag", "label"),
                "labels": "tag",
                "more": ("tag", "label", "extra"),
            },
        )
        other = shelf_view(
            {"labels": HistoryStream(ShelfLabel, path="shelf"), "items": HistoryStream(ShelfItem, path="shelf")},
            {"labels": ("tag", "nothing"), "items": ("name", "title")},
        )

        assert history_part_problems(view) + history_part_problems(other) == [
            "WrongShelfViewSet.history_detail['missing'] names no stream of history_also.",
            "WrongShelfViewSet.history_detail['plain'] names no stream of history_also.",
            "WrongShelfViewSet.history_detail['labels'] is 'tag', not (foreign key, attribute).",
            "WrongShelfViewSet.history_detail['more'] is ('tag', 'label', 'extra'), not (foreign key, attribute).",
            "WrongShelfViewSet.history_detail['labels']: testapp.Tag has no 'nothing'.",
            "WrongShelfViewSet.history_detail['items']: 'name' isn't a foreign key of testapp.ShelfItem.",
        ]

    def test_a_detail_through_any_key_to_any_attribute_is_fine(self):
        view = shelf_view({"labels": HistoryStream(ShelfLabel, path="shelf")}, {"labels": ("moved_from", "name")})

        assert history_part_problems(view) == []

    def test_a_detail_of_a_stream_not_a_history_stream(self):
        view = shelf_view({"plain": "items"}, {"plain": ("tag", "label")})

        assert (
            history_part_problems(view)[-1]
            == "WrongShelfViewSet.history_detail['plain'] names no stream of history_also."
        )

    @pytest.mark.parametrize("built", ["history_filterset_class", "history_serializer_class"])
    def test_refused_when_the_view_is_built(self, built):
        view = shelf_view({"plain": "items"}, {"missing": ("tag", "label")})

        with pytest.raises(ImproperlyConfigured) as raised:
            getattr(view, built)

        assert str(raised.value) == (
            "WrongShelfViewSet.history_also['plain'] is 'items', not a HistoryStream. "
            "WrongShelfViewSet.history_detail['missing'] names no stream of history_also."
        )

    def test_a_view_of_one_stream_with_a_detail_is_refused(self):
        with pytest.raises(ImproperlyConfigured):
            assert shelf_view({}, {"labels": ("tag", "label")}).history_serializer_class


class TestTheSchema:
    @pytest.fixture
    def component(self):
        from django.test import override_settings
        from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
        from drf_spectacular.generators import SchemaGenerator
        from rest_framework.routers import SimpleRouter

        router = SimpleRouter()
        router.register("shelves", ShelfViewSet, basename="shelf")
        reset_generator_stats()
        with override_settings(REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "isik.django.drf.spectacular.AutoSchema"}):
            schema = SchemaGenerator(patterns=router.urls).get_schema(request=None, public=True)
        assert not GENERATOR_STATS._warn_cache
        assert not GENERATOR_STATS._error_cache
        return schema["components"]["schemas"]["ShelfHistory"]["properties"]

    def test_documents_every_row_field_without_a_warning(self, component):
        assert component["part"] == {"type": "string", "readOnly": True}
        for name in ("subject_id", "subject_label", "detail"):
            assert component[name] == {"type": "string", "readOnly": True, "nullable": True}
        assert component["changes"] == {
            "type": "object",
            "additionalProperties": {},
            "readOnly": True,
            "nullable": True,
        }

    def test_the_owners_own_fields_are_null_on_another_streams_row(self, component):
        assert component["name"]["nullable"] is True
        assert component["id"]["nullable"] is True
        assert "nullable" not in component["event_id"]
        assert "nullable" not in component["action"]

    def test_a_uuid_is_compared_as_the_text_its_stored_as(self):
        import uuid

        import pghistory

        from isik.django.drf.viewsets.history import _JSONContextUUIDFilter, context_filter

        mine, other = uuid.uuid4(), uuid.uuid4()
        with pghistory.context(user=str(mine)):
            Box.objects.create(name="mine")
        with pghistory.context(user=str(other)):
            Box.objects.create(name="theirs")
        actor = context_filter("user", filter_cls=_JSONContextUUIDFilter)

        assert [event.pgh_data["name"] for event in actor.filter(Events.objects.all(), mine)] == ["mine"]
        assert actor.filter(Events.objects.all(), None).count() == 2


class TestTheParts:
    def test_the_scope_is_each_stream_by_its_own_path(self):
        import uuid

        from django.db.models import Q

        pk = uuid.uuid4()

        assert ShelfViewSet.history_scope([pk]) == (
            Q(pgh_model="testapp.ShelfEvent", pgh_obj_id__in=[str(pk)])
            | Q(pgh_model="testapp.ShelfItemEvent", pgh_data__shelf_id__in=[str(pk)])
            | Q(pgh_model="testapp.ShelfLabelEvent", pgh_data__shelf_id__in=[str(pk)])
        )

    def test_a_key_in_the_json_is_a_number_or_text(self):
        import uuid

        from isik.django.drf.viewsets.history import _json_value

        pk = uuid.uuid4()

        assert _json_value(7) == 7
        assert _json_value(pk) == str(pk)

    def test_a_row_without_its_path_is_about_nothing(self):
        import types

        subject = ShelfViewSet.history_serializer_class.subject_of
        row = types.SimpleNamespace(pgh_model="testapp.ShelfItemEvent", pgh_data={}, pgh_obj_id=1)

        assert subject(row) is None

    def test_without_a_page_context_a_row_names_nothing(self):
        import types

        from isik.django.drf.viewsets.history import _detail, _subject_label_of

        row = types.SimpleNamespace(pgh_model="testapp.ShelfLabelEvent", pgh_data={"shelf_id": "x", "tag_id": "y"})

        assert _subject_label_of(lambda event: "x")(row, {}) is None
        assert _subject_label_of(lambda event: "x")(row, {"history_page": {}}) is None
        assert _detail(row, {}) is None
        assert _detail(row, {"history_page": {}}) is None

    def test_the_actor_filter_follows_a_uuid_user_model(self, monkeypatch):
        from isik.django.drf.viewsets import history as history_views

        monkeypatch.setattr(history_views, "get_user_model", lambda: Widget)
        monkeypatch.setattr(history_views, "history_middleware_installed", lambda: True)

        actor = BoxViewSet.default_history_filters()["actor"]

        assert type(actor).__name__ == "_JSONContextUUIDFilter"
        assert actor.field_name == "pgh_context__user"
