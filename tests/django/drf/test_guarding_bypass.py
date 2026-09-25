"""
No write path can skip a viewset's field guards: an isik serializer checks them inside save() - before
the write - however it was built, and anything else that succeeds without running them fails inside
a transaction, so its database writes roll back.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.viewsets import ModelViewSet

from isik.django.drf.permissions import BasePermission, IsSuperUser, guarding
from isik.django.drf.serializers.base import BaseModelSerializer
from isik.django.drf.serializers.guarded_save import FieldGuardsOnSaveMixin, _active_guarded_view
from isik.django.drf.viewsets import GuardedFieldsMixin, writes_no_guarded_fields
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db

GUARD = guarding(IsSuperUser, fields=["count"])


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="password")


@pytest.fixture
def root(django_user_model):
    return django_user_model.objects.create_superuser(username="root", email="root@example.com", password="password")


@pytest.fixture
def widget():
    return Widget.objects.create(name="bolt", count=1)


def isik_serializer(name):
    """A fresh BaseModelSerializer per viewset, so the shared-serializer check never compares them."""
    return type(
        name,
        (BaseModelSerializer,),
        {
            "exempt_from_registry": True,
            "Meta": type("Meta", (), {"model": Widget, "fields": ["id", "name", "count"]}),
        },
    )


def plain_serializer(name):
    return type(
        name,
        (serializers.ModelSerializer,),
        {
            "Meta": type("Meta", (), {"model": Widget, "fields": ["id", "name", "count"]}),
        },
    )


def call(viewset, user, method, action_name, data=None, pk=None):
    factory = APIRequestFactory()
    path = f"/widgets/{pk}/" if pk else "/widgets/"
    request = getattr(factory, method)(path, data or {}, format="json")
    force_authenticate(request, user=user)
    return viewset.as_view({method: action_name})(request, **({"pk": pk} if pk else {}))


def guarded(name, serializer_cls, body=None, permission_classes=(GUARD,)):
    attrs = {
        "queryset": Widget.objects.all(),
        "serializer_class": serializer_cls,
        "permission_classes": permission_classes,
    }
    return type(name, (GuardedFieldsMixin, ModelViewSet), {**attrs, **(body or {})})


class TestSaveHook:
    """Layer one: FieldGuardsOnSaveMixin checks before the write, however the serializer was built."""

    def test_a_handler_building_its_own_serializer_is_still_checked(self, bob, widget):
        serializer_cls = isik_serializer("HandBuilt")

        def partial_update(self, request, pk=None):
            body = serializer_cls(self.get_object(), data=request.data, partial=True)
            body.is_valid(raise_exception=True)
            body.save()
            return Response(body.data)

        viewset = guarded("HandBuiltViewSet", serializer_cls, {"partial_update": partial_update})
        response = call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"count": ["You may not change this field."]}
        widget.refresh_from_db()
        assert widget.count == 1

    def test_a_custom_action_building_its_own_serializer_is_still_checked(self, bob, widget):
        serializer_cls = isik_serializer("ForAnAction")

        @action(detail=True, methods=["post"])
        def recount(self, request, pk=None):
            body = serializer_cls(self.get_object(), data={"count": request.data["count"]}, partial=True)
            body.is_valid(raise_exception=True)
            body.save()
            return Response(body.data)

        viewset = guarded("RecountViewSet", serializer_cls, {"recount": recount})
        assert call(viewset, bob, "post", "recount", {"count": 2}, pk=widget.pk).status_code == 400
        widget.refresh_from_db()
        assert widget.count == 1

    def test_values_passed_to_save_count_as_part_of_the_write(self, bob, root, widget):
        serializer_cls = isik_serializer("SaveKwargs")

        def perform_update(self, serializer):
            serializer.save(count=99)  # a server-side value, but a change to a guarded field all the same

        viewset = guarded("SaveKwargsViewSet", serializer_cls, {"perform_update": perform_update})
        response = call(viewset, bob, "patch", "partial_update", {"name": "nut"}, pk=widget.pk)
        assert response.data == {"count": ["You may not change this field."]}
        assert call(viewset, root, "patch", "partial_update", {"name": "nut"}, pk=widget.pk).status_code == 200
        widget.refresh_from_db()
        assert widget.count == 99

    def test_a_save_value_for_an_unguarded_field_is_fine(self, bob, widget):
        serializer_cls = isik_serializer("SaveUnguarded")

        def perform_update(self, serializer):
            serializer.save(name="set by the server")

        viewset = guarded("SaveUnguardedViewSet", serializer_cls, {"perform_update": perform_update})
        assert call(viewset, bob, "patch", "partial_update", {"name": "nut"}, pk=widget.pk).status_code == 200

    def test_the_default_path_asks_the_predicate_once(self, bob, widget):
        asked = []

        class Counting(BasePermission):
            def has_permission(self, request, view):
                asked.append(view.action)
                return True

        viewset = guarded(
            "CountingViewSet",
            isik_serializer("Counted"),
            permission_classes=[
                guarding(Counting, fields=["count"]),
            ],
        )
        asked.clear()  # has_permission of the guard itself doesn't ask the predicate
        assert call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk).status_code == 200
        assert asked == ["partial_update"]

    def test_outside_a_request_saving_checks_nothing(self, widget):
        body = isik_serializer("Standalone")(widget, data={"count": 2}, partial=True)
        body.is_valid(raise_exception=True)
        body.save()
        widget.refresh_from_db()
        assert widget.count == 2

    def test_the_active_view_is_cleared_after_every_request(self, bob, widget):
        viewset = guarded("ClearedViewSet", isik_serializer("Cleared"))
        call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk)
        assert _active_guarded_view.get() is None

        def partial_update(self, request, pk=None):
            raise RuntimeError("boom")

        failing = guarded("FailingViewSet", isik_serializer("Failing"), {"partial_update": partial_update})
        with pytest.raises(RuntimeError):
            call(failing, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk)
        assert _active_guarded_view.get() is None

    def test_a_safe_request_sets_no_active_view(self, bob, widget):
        seen = []

        def retrieve(self, request, pk=None):
            seen.append(_active_guarded_view.get())
            return Response({})

        viewset = guarded("SafeViewSet", isik_serializer("Safe"), {"retrieve": retrieve})
        call(viewset, bob, "get", "retrieve", pk=widget.pk)
        assert seen == [None]

    def test_the_mixin_is_part_of_base_model_serializer(self):
        assert issubclass(BaseModelSerializer, FieldGuardsOnSaveMixin)


class TestAfterTheFact:
    """Layer two: a write that succeeds without its guards having run fails, and rolls back."""

    def test_a_plain_serializer_saved_by_hand_fails_and_rolls_back(self, bob, widget):
        serializer_cls = plain_serializer("PlainByHand")

        def partial_update(self, request, pk=None):
            body = serializer_cls(self.get_object(), data=request.data, partial=True)
            body.is_valid(raise_exception=True)
            body.save()
            return Response(body.data)

        viewset = guarded("PlainByHandViewSet", serializer_cls, {"partial_update": partial_update})
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^PlainByHandViewSet\.partial_update succeeded without running its field guards, so its "
            r"database writes are rolled back\.",
        ):
            call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk)
        widget.refresh_from_db()
        assert widget.count == 1

    def test_an_orm_write_in_a_custom_action_fails_and_rolls_back(self, bob, widget):
        @action(detail=True, methods=["post"])
        def bump(self, request, pk=None):
            Widget.objects.filter(pk=pk).update(count=50)
            Widget.objects.create(name="side effect")
            return Response({})

        viewset = guarded("BumpViewSet", plain_serializer("Bumped"), {"bump": bump})
        with pytest.raises(ImproperlyConfigured, match=r"^BumpViewSet\.bump succeeded without"):
            call(viewset, bob, "post", "bump", pk=widget.pk)
        widget.refresh_from_db()
        assert widget.count == 1
        assert not Widget.objects.filter(name="side effect").exists()

    @pytest.mark.parametrize("decorators_outer_first", [True, False])
    def test_writes_no_guarded_fields_opts_an_action_out(self, bob, widget, decorators_outer_first):
        def archive(self, request, pk=None):
            Widget.objects.filter(pk=pk).update(name="archived")
            return Response({})

        if decorators_outer_first:
            archive = writes_no_guarded_fields(action(detail=True, methods=["post"])(archive))
        else:
            archive = action(detail=True, methods=["post"])(writes_no_guarded_fields(archive))
        viewset = guarded("ArchiveViewSet", plain_serializer("Archived"), {"archive": archive})
        assert call(viewset, bob, "post", "archive", pk=widget.pk).status_code == 200
        widget.refresh_from_db()
        assert widget.name == "archived"

    def test_the_decorator_marks_and_returns_the_same_function(self):
        def handler():
            pass

        assert writes_no_guarded_fields(handler) is handler
        assert handler.writes_no_guarded_fields is True

    def test_calling_the_check_by_hand_satisfies_it(self, bob, root, widget):
        serializer_cls = plain_serializer("CheckedByHand")

        def partial_update(self, request, pk=None):
            body = serializer_cls(self.get_object(), data=request.data, partial=True)
            body.is_valid(raise_exception=True)
            self.check_guarded_fields(body)
            body.save()
            return Response(body.data)

        viewset = guarded("CheckedByHandViewSet", serializer_cls, {"partial_update": partial_update})
        assert call(viewset, bob, "patch", "partial_update", {"name": "nut"}, pk=widget.pk).status_code == 200
        assert call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk).status_code == 400
        assert call(viewset, root, "patch", "partial_update", {"count": 2}, pk=widget.pk).status_code == 200

    def test_a_plain_serializer_through_the_default_handlers_is_checked(self, bob, widget):
        viewset = guarded("PlainDefaultViewSet", plain_serializer("PlainDefault"))
        assert call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk).status_code == 400
        assert call(viewset, bob, "post", "create", {"name": "nut", "count": 2}).status_code == 400
        assert call(viewset, bob, "post", "create", {"name": "nut"}).status_code == 201

    def test_destroy_writes_no_fields(self, bob, widget):
        viewset = guarded("DestroyViewSet", plain_serializer("Destroyed"))
        assert call(viewset, bob, "delete", "destroy", pk=widget.pk).status_code == 204

    def test_more_exempt_actions_can_be_declared(self, bob, widget):
        @action(detail=True, methods=["post"])
        def touch(self, request, pk=None):
            return Response({})

        viewset = guarded(
            "TouchViewSet",
            plain_serializer("Touched"),
            {
                "touch": touch,
                "actions_writing_no_guarded_fields": ("destroy", "touch"),
            },
        )
        assert call(viewset, bob, "post", "touch", pk=widget.pk).status_code == 200

    def test_a_failed_request_is_left_alone(self, bob, widget):
        @action(detail=True, methods=["post"])
        def refuse(self, request, pk=None):
            return Response({"detail": "no"}, status=status.HTTP_409_CONFLICT)

        viewset = guarded("RefuseViewSet", plain_serializer("Refused"), {"refuse": refuse})
        assert call(viewset, bob, "post", "refuse", pk=widget.pk).status_code == 409

    def test_an_action_whose_permissions_drop_the_field_guards_is_left_alone(self, bob, widget):
        @action(detail=True, methods=["post"])
        def poke(self, request, pk=None):
            return Response({})

        def get_permissions(self):
            if self.action == "poke":
                return []
            return super(type(self), self).get_permissions()

        viewset = guarded("PokeViewSet", plain_serializer("Poked"), {"poke": poke, "get_permissions": get_permissions})
        assert call(viewset, bob, "post", "poke", pk=widget.pk).status_code == 200

    def test_an_exception_after_a_write_rolls_it_back(self, bob, widget):
        @action(detail=True, methods=["post"])
        def half(self, request, pk=None):
            Widget.objects.filter(pk=pk).update(count=77)
            raise RuntimeError("halfway")

        viewset = guarded("HalfViewSet", plain_serializer("Half"), {"half": half})
        with pytest.raises(RuntimeError):
            call(viewset, bob, "post", "half", pk=widget.pk)
        widget.refresh_from_db()
        assert widget.count == 1


class TestDispatch:
    @pytest.mark.parametrize(("method", "handler_name"), [("get", "retrieve"), ("patch", "partial_update")])
    def test_positional_url_arguments_reach_the_handler(self, bob, widget, method, handler_name):
        received = []

        def handler(self, request, *args, **kwargs):
            received.append(args)
            return Response({})

        viewset = guarded(
            f"Positional{handler_name}",
            plain_serializer(f"Positional{handler_name}Serializer"),
            {
                handler_name: writes_no_guarded_fields(handler),
            },
        )
        request = getattr(APIRequestFactory(), method)(f"/widgets/{widget.pk}/", {}, format="json")
        force_authenticate(request, user=bob)
        viewset.as_view({method: handler_name})(request, "positional", pk=widget.pk)
        assert received == [("positional",)]

    def test_the_whole_error_message(self, bob, widget):
        @action(detail=True, methods=["post"])
        def silent(self, request, pk=None):
            return Response({})

        viewset = guarded("SilentViewSet", plain_serializer("Silent"), {"silent": silent})
        with pytest.raises(ImproperlyConfigured) as raised:
            call(viewset, bob, "post", "silent", pk=widget.pk)
        assert str(raised.value) == (
            "SilentViewSet.silent succeeded without running its field guards, so its database writes are rolled "
            "back. Save through a serializer with FieldGuardsOnSaveMixin (BaseModelSerializer), call "
            "self.check_guarded_fields(serializer), or mark the action @writes_no_guarded_fields if it writes no "
            "guarded field."
        )

    @pytest.mark.parametrize("code", [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED])
    def test_every_error_status_is_left_alone(self, bob, widget, code):
        @action(detail=True, methods=["post"])
        def fail(self, request, pk=None):
            return Response({}, status=code)

        viewset = guarded(f"Fail{code}ViewSet", plain_serializer(f"Fail{code}"), {"fail": fail})
        assert call(viewset, bob, "post", "fail", pk=widget.pk).status_code == code

    def test_a_399_still_counts_as_success(self, bob, widget):
        @action(detail=True, methods=["post"])
        def odd(self, request, pk=None):
            return Response({}, status=399)

        viewset = guarded("OddViewSet", plain_serializer("Odd"), {"odd": odd})
        with pytest.raises(ImproperlyConfigured):
            call(viewset, bob, "post", "odd", pk=widget.pk)


class TestTransaction:
    @staticmethod
    def depth_during(viewset_name, user, pk, **attrs):
        depths = []

        @writes_no_guarded_fields
        @action(detail=True, methods=["post"])
        def probe(self, request, pk=None):
            depths.append(len(connection.atomic_blocks))
            return Response({})

        viewset = guarded(viewset_name, plain_serializer(f"{viewset_name}Serializer"), {"probe": probe, **attrs})
        call(viewset, user, "post", "probe", pk=pk)
        return depths[0]

    def test_it_opens_one_transaction_per_database_it_names(self, bob, widget, monkeypatch):
        from contextlib import nullcontext

        from isik.django.drf.viewsets import guarded_fields

        opened = []

        def atomic(using=None):
            opened.append(using)
            return nullcontext()

        monkeypatch.setattr(guarded_fields.transaction, "atomic", atomic)
        self.depth_during("EveryAlias", bob, widget.pk)
        self.depth_during("TwoAliases", bob, widget.pk, field_guard_databases=["primary", "replica"])
        assert opened == ["default", "primary", "replica"]

    def test_a_write_runs_in_its_own_transaction_on_every_database(self, bob, widget):
        outside = len(connection.atomic_blocks)
        assert self.depth_during("EveryDatabase", bob, widget.pk) == outside + 1

    def test_field_guard_databases_narrows_it(self, bob, widget):
        outside = len(connection.atomic_blocks)
        assert self.depth_during("NoDatabase", bob, widget.pk, field_guard_databases=[]) == outside
        assert self.depth_during("DefaultOnly", bob, widget.pk, field_guard_databases=["default"]) == outside + 1

    def test_a_viewset_without_field_guards_gets_no_transaction(self, bob, widget):
        depths = []

        @action(detail=True, methods=["post"])
        def probe(self, request, pk=None):
            depths.append(len(connection.atomic_blocks))
            return Response({})

        viewset = guarded(
            "ActionsOnly",
            plain_serializer("ActionsOnlySerializer"),
            {"probe": probe},
            permission_classes=[guarding(IsSuperUser, actions=["destroy"])],
        )
        outside = len(connection.atomic_blocks)
        assert call(viewset, bob, "post", "probe", pk=widget.pk).status_code == 200
        assert depths == [outside]

    def test_a_safe_request_gets_no_transaction(self, bob, widget):
        depths = []

        def retrieve(self, request, pk=None):
            depths.append(len(connection.atomic_blocks))
            return Response({})

        viewset = guarded("SafeNoTransaction", plain_serializer("SafeNoTransactionSerializer"), {"retrieve": retrieve})
        outside = len(connection.atomic_blocks)
        call(viewset, bob, "get", "retrieve", pk=widget.pk)
        assert depths == [outside]


class TestManyTrue:
    def create_many(self, viewset, user, rows):
        request = APIRequestFactory().post("/widgets/", rows, format="json")
        force_authenticate(request, user=user)
        return viewset.as_view({"post": "create"})(request)

    @pytest.mark.parametrize("serializer_factory", [plain_serializer, isik_serializer])
    def test_each_row_is_checked_as_a_new_one(self, bob, root, serializer_factory):
        def get_serializer(self, *args, **kwargs):
            kwargs["many"] = isinstance(kwargs.get("data"), list)
            return super(type(self), self).get_serializer(*args, **kwargs)

        name = f"Many{serializer_factory.__name__}"
        viewset = guarded(f"{name}ViewSet", serializer_factory(name), {"get_serializer": get_serializer})
        assert self.create_many(viewset, bob, [{"name": "a"}, {"name": "b"}]).status_code == 201
        response = self.create_many(viewset, bob, [{"name": "c"}, {"name": "d", "count": 3}])
        assert response.status_code == 400
        assert response.data == {"count": ["You may not change this field."]}
        assert not Widget.objects.filter(name__in=["c", "d"]).exists()
        assert self.create_many(viewset, root, [{"name": "e", "count": 3}]).status_code == 201

    def test_every_row_is_new_so_an_object_predicate_is_unknown_and_allows(self, bob):
        from isik.django.drf.permissions import is_owner

        def get_serializer(self, *args, **kwargs):
            kwargs["many"] = isinstance(kwargs.get("data"), list)
            return super(type(self), self).get_serializer(*args, **kwargs)

        viewset = guarded(
            "ManyOwnedViewSet",
            plain_serializer("ManyOwned"),
            {"get_serializer": get_serializer},
            permission_classes=[guarding(is_owner("owner"), fields=["count"])],
        )
        assert self.create_many(viewset, bob, [{"name": "a", "count": 3}]).status_code == 201

    def test_save_values_merge_into_every_row(self, bob):
        from rest_framework.serializers import ListSerializer

        serializer_cls = plain_serializer("ManyWithKwargs")
        view = guarded("ManyWithKwargsViewSet", serializer_cls)()
        view.request = type("Request", (), {"user": bob})()
        view.format_kwarg = None
        body = serializer_cls(data=[{"name": "a"}], many=True)
        body.is_valid(raise_exception=True)
        assert isinstance(body, ListSerializer)
        view.check_guarded_fields(body)
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            view.check_guarded_fields(body, save_kwargs={"count": 4})


class TestRefusedRequestsRollBack:
    """A request DRF answered from an exception keeps none of the writes its handler made first."""

    def test_a_refused_save_takes_earlier_writes_with_it(self, bob, widget, django_capture_on_commit_callbacks):
        serializer_cls = isik_serializer("AuditedFirst")
        fired = []

        def partial_update(self, request, pk=None):
            Widget.objects.create(name="audit row")
            transaction.on_commit(lambda: fired.append("sent"))
            body = serializer_cls(self.get_object(), data=request.data, partial=True)
            body.is_valid(raise_exception=True)
            body.save()
            return Response(body.data)

        viewset = guarded("AuditedFirstViewSet", serializer_cls, {"partial_update": partial_update})
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            response = call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk)
        assert response.status_code == 400
        assert not Widget.objects.filter(name="audit row").exists()
        assert (callbacks, fired) == ([], [])

    def test_a_failed_validation_after_a_write_rolls_it_back_too(self, bob, widget):
        serializer_cls = isik_serializer("ValidatesLate")

        def partial_update(self, request, pk=None):
            Widget.objects.create(name="written before validating")
            body = serializer_cls(self.get_object(), data={"count": "not a number"}, partial=True)
            body.is_valid(raise_exception=True)
            return Response(body.data)

        viewset = guarded("ValidatesLateViewSet", serializer_cls, {"partial_update": partial_update})
        assert call(viewset, bob, "patch", "partial_update", {}, pk=widget.pk).status_code == 400
        assert not Widget.objects.filter(name="written before validating").exists()

    def test_an_error_response_returned_on_purpose_keeps_its_writes(self, bob, widget):
        @writes_no_guarded_fields
        @action(detail=True, methods=["post"])
        def throttle(self, request, pk=None):
            Widget.objects.create(name="attempt counted")
            return Response({"detail": "slow down"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        viewset = guarded("ThrottleViewSet", plain_serializer("Throttled"), {"throttle": throttle})
        assert call(viewset, bob, "post", "throttle", pk=widget.pk).status_code == 429
        assert Widget.objects.filter(name="attempt counted").exists()

    def test_a_plain_django_response_keeps_its_writes(self, bob, widget):
        from django.http import HttpResponse

        @writes_no_guarded_fields
        @action(detail=True, methods=["post"])
        def raw(self, request, pk=None):
            Widget.objects.create(name="raw response write")
            return HttpResponse("ok")

        viewset = guarded("RawViewSet", plain_serializer("Raw"), {"raw": raw})
        assert call(viewset, bob, "post", "raw", pk=widget.pk).status_code == 200
        assert Widget.objects.filter(name="raw response write").exists()

    def test_a_successful_write_commits_and_runs_its_callbacks(self, bob, widget, django_capture_on_commit_callbacks):
        fired = []

        def perform_update(self, serializer):
            serializer.save()
            transaction.on_commit(lambda: fired.append("sent"))

        viewset = guarded("CommitsViewSet", isik_serializer("Commits"), {"perform_update": perform_update})
        with django_capture_on_commit_callbacks(execute=True):
            assert call(viewset, bob, "patch", "partial_update", {"name": "nut"}, pk=widget.pk).status_code == 200
        assert fired == ["sent"]

    def test_the_rollback_covers_every_database_it_opened(self, bob, widget, monkeypatch):
        from contextlib import nullcontext

        from isik.django.drf.viewsets import guarded_fields

        rolled_back = []
        monkeypatch.setattr(guarded_fields.transaction, "atomic", lambda using=None: nullcontext())
        monkeypatch.setattr(
            guarded_fields.transaction, "set_rollback", lambda value, using=None: rolled_back.append((value, using))
        )
        viewset = guarded(
            "TwoDatabasesViewSet",
            plain_serializer("TwoDatabases"),
            {
                "field_guard_databases": ["primary", "replica"],
            },
        )
        assert call(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk).status_code == 400
        assert rolled_back == [(True, "primary"), (True, "replica")]


class TestOnlyTheViewsetsModel:
    """The save hook holds a serializer to the guards only when it writes this viewset's model."""

    def test_another_models_serializer_saved_along_the_way_is_not_checked(self, bob, widget):
        from tests.testapp.models import Tag

        class TagSerializer(serializers.ModelSerializer):
            count = serializers.CharField(source="label")  # same name as the guarded field, different model

            class Meta:
                model = Tag
                fields = ["id", "count"]

        tag_serializer_cls = type("TagWithCount", (FieldGuardsOnSaveMixin, TagSerializer), {})
        serializer_cls = isik_serializer("AlongsideATag")

        def partial_update(self, request, pk=None):
            tag = tag_serializer_cls(data={"count": "urgent"})
            tag.is_valid(raise_exception=True)
            tag.save()
            body = serializer_cls(self.get_object(), data=request.data, partial=True)
            body.is_valid(raise_exception=True)
            body.save()
            return Response(body.data)

        viewset = guarded("AlongsideATagViewSet", serializer_cls, {"partial_update": partial_update})
        assert call(viewset, bob, "patch", "partial_update", {"name": "nut"}, pk=widget.pk).status_code == 200
        assert Tag.objects.filter(label="urgent").exists()

    def test_only_another_models_save_does_not_count_as_running_the_guards(self, bob, widget):
        from tests.testapp.models import Tag

        tag_serializer_cls = type(
            "OnlyATag",
            (FieldGuardsOnSaveMixin, serializers.ModelSerializer),
            {
                "Meta": type("Meta", (), {"model": Tag, "fields": ["id", "label"]}),
            },
        )

        @action(detail=True, methods=["post"])
        def tag_it(self, request, pk=None):
            tag = tag_serializer_cls(data={"label": "x"})
            tag.is_valid(raise_exception=True)
            tag.save()
            Widget.objects.filter(pk=pk).update(count=9)
            return Response({})

        viewset = guarded("OnlyATagViewSet", plain_serializer("OnlyATagWidget"), {"tag_it": tag_it})
        with pytest.raises(ImproperlyConfigured, match="succeeded without running its field guards"):
            call(viewset, bob, "post", "tag_it", pk=widget.pk)
        widget.refresh_from_db()
        assert widget.count == 1

    def test_a_serializer_with_no_model_is_not_checked(self, widget):
        view = guarded("NoModelViewSet", plain_serializer("NoModelSerializer"))()
        assert view.guards_serializer(serializers.Serializer()) is False

    def test_the_viewsets_own_model_attribute_is_preferred(self):
        from tests.testapp.models import Tag

        view = guarded("ModelAttrViewSet", plain_serializer("ModelAttrSerializer"), {"model": Tag})()
        tag_serializer = type(
            "TagOnly",
            (serializers.ModelSerializer,),
            {
                "Meta": type("Meta", (), {"model": Tag, "fields": ["id"]}),
            },
        )()
        assert view.guards_serializer(tag_serializer) is True
        assert view.guards_serializer(plain_serializer("WidgetNotTag")()) is False

    def test_a_proxy_of_the_model_is_the_same_model(self):
        from types import SimpleNamespace

        # a proxy's _meta.concrete_model is the model it proxies - all the check reads
        proxy = type("WidgetProxy", (), {"_meta": SimpleNamespace(concrete_model=Widget)})
        proxy_serializer = SimpleNamespace(Meta=SimpleNamespace(model=proxy))
        view = guarded("ProxyViewSet", plain_serializer("ProxyWidget"))()
        assert view.guards_serializer(proxy_serializer) is True
