import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from rest_framework import serializers, status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.viewsets import ModelViewSet

from isik.django.drf.permissions import (
    Guard,
    IsSuperUser,
    guarding,
    is_owner,
    object_property,
    user_property,
)
from isik.django.drf.viewsets import BaseModelViewSet, GuardedFieldsMixin
from isik.django.drf.viewsets.guarded_fields import _MISSING, _changed, _dig
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="password")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="password")


@pytest.fixture
def root(django_user_model):
    return django_user_model.objects.create_superuser(username="root", email="root@example.com", password="password")


def request_by(rf, user):
    request = rf.get("/")
    request.user = user
    return request


class RecordsView(BasePermission):
    """A leaf that records which view (and object) it was asked about - every operator has to forward both."""

    seen = []

    def has_permission(self, request, view):
        self.seen.append(("permission", view))
        return True

    def has_object_permission(self, request, view, obj):
        self.seen.append(("object", view, obj))
        return True


@pytest.fixture
def seen():
    RecordsView.seen = []
    return RecordsView.seen


class FakeView:
    def __init__(self, action, runs_field_guards=False):
        self.action = action
        self.runs_field_guards = runs_field_guards


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count", "owner"]
        read_only_fields = ["id"]


def widget_viewset(*permission_classes, serializer=WidgetSerializer):
    return type(
        "GuardedWidgetViewSet",
        (GuardedFieldsMixin, ModelViewSet),
        {"queryset": Widget.objects.all(), "serializer_class": serializer, "permission_classes": permission_classes},
    )


def patch(viewset, widget, user, data, format="json"):
    request = APIRequestFactory().patch(f"/widgets/{widget.pk}/", data, format=format)
    force_authenticate(request, user=user)
    return viewset.as_view({"patch": "partial_update"})(request, pk=widget.pk)


def post(viewset, user, data):
    request = APIRequestFactory().post("/widgets/", data, format="json")
    force_authenticate(request, user=user)
    return viewset.as_view({"post": "create"})(request)


class TestGuardingArguments:
    def test_requires_exactly_one_of_fields_or_actions(self):
        with pytest.raises(ValueError, match="^guarding requires exactly one of fields, setting or actions$"):
            guarding(IsSuperUser)
        with pytest.raises(ValueError, match="^guarding requires exactly one of fields, setting or actions$"):
            guarding(IsSuperUser, fields=["a"], actions=["b"])

    def test_rejects_an_empty_scope(self):
        with pytest.raises(ValueError, match="^guarding requires at least one field or action$"):
            guarding(IsSuperUser, fields=[])
        with pytest.raises(ValueError, match="^guarding requires at least one field or action$"):
            guarding(IsSuperUser, actions=())

    def test_rejects_a_bare_string_which_would_otherwise_scope_to_its_characters(self):
        with pytest.raises(ValueError, match="not a single string"):
            guarding(IsSuperUser, fields="count")
        with pytest.raises(ValueError, match="^guarding takes a list of fields or actions, not a single string$"):
            guarding(IsSuperUser, actions="destroy")

    def test_generated_class_name_names_the_predicate_and_scope(self):
        assert guarding(IsSuperUser, actions=["destroy", "create"]).__name__ == ("IsSuperUserForCreateAndDestroy")
        assert guarding(is_owner("owner"), fields=["count"]).__name__ == ("IsOwnerByOwnerForCount")

    def test_generated_class_name_for_a_composed_predicate(self):
        assert guarding(~IsSuperUser, actions=["destroy"]).__name__ == "NotIsSuperUserForDestroy"
        assert guarding(is_owner("owner") | IsSuperUser, actions=["destroy"]).__name__ == (
            "IsOwnerByOwnerOrIsSuperUserForDestroy"
        )
        assert guarding(~(IsSuperUser & IsAuthenticated), fields=["name"]).__name__ == (
            "NotIsSuperUserAndIsAuthenticatedForName"
        )

    def test_an_explicit_name(self):
        assert guarding(IsSuperUser, actions=["destroy"], name="SuperusersDelete").__name__ == "SuperusersDelete"

    def test_setting_counts_toward_exactly_one(self):
        with pytest.raises(ValueError, match="exactly one of fields, setting or actions"):
            guarding(IsSuperUser, fields=["a"], setting={"a": 1})
        with pytest.raises(ValueError, match="exactly one of fields, setting or actions"):
            guarding(IsSuperUser, setting={"a": 1}, actions=["b"])

    def test_an_empty_setting_is_refused(self):
        with pytest.raises(ValueError, match="^guarding requires at least one field or action$"):
            guarding(IsSuperUser, setting={})

    def test_fields_refuses_a_dict_and_points_at_setting(self):
        with pytest.raises(ValueError, match="^guarding's fields= takes field names - for target values use setting=$"):
            guarding(IsSuperUser, fields={"count": 5})

    def test_setting_refuses_anything_but_a_dict(self):
        with pytest.raises(ValueError, match="^guarding's setting= takes a dict of field names to target values$"):
            guarding(IsSuperUser, setting=["count"])

    def test_setting_name_and_message(self):
        guard = guarding(IsSuperUser, setting={"name": "x", "count": guarding.values(5, 7)})
        assert guard.__name__ == "IsSuperUserForSettingCountAndName"
        assert guard.message == "You may not change this field."

    def test_a_flat_field_list_guards_any_value_and_setting_guards_its_targets(self):
        from isik.django.drf.permissions import ANY_VALUE

        assert guarding(IsSuperUser, fields=["count"]).fields == {"count": ANY_VALUE}
        target = guarding(IsSuperUser, setting={"count": 5}).fields["count"]
        assert (target.matches(5), target.matches(6)) == (True, False)

    def test_an_explicit_any_value_target_passes_through(self):
        from isik.django.drf.permissions import ANY_VALUE

        assert guarding(IsSuperUser, setting={"count": ANY_VALUE}).fields == {"count": ANY_VALUE}

    def test_default_messages_depend_on_the_scope(self):
        assert guarding(IsSuperUser, fields=["count"]).message == "You may not change this field."
        assert guarding(IsSuperUser, actions=["destroy"]).message == "You may not perform this action."

    def test_a_custom_message(self):
        assert guarding(IsSuperUser, actions=["destroy"], message="nope").message == "nope"

    def test_is_a_guard(self):
        assert issubclass(guarding(IsSuperUser, actions=["destroy"]), Guard)

    @pytest.mark.parametrize(
        "compose",
        [
            lambda guard: guard | IsSuperUser,
            lambda guard: guard & IsSuperUser,
            lambda guard: ~guard,
            # A permission class on the left too: the guard's metaclass subclasses DRF's, so
            # Python asks its reflected __rand__/__ror__ first.
            lambda guard: IsSuperUser | guard,
            lambda guard: IsSuperUser & guard,
        ],
    )
    def test_refuses_to_be_composed(self, compose):
        with pytest.raises(TypeError, match="compose the predicate inside guarding"):
            compose(guarding(IsSuperUser, actions=["destroy"]))


class TestTargets:
    def test_any_value_matches_everything(self):
        from isik.django.drf.permissions import ANY_VALUE

        assert ANY_VALUE.matches(None) is True
        assert repr(ANY_VALUE) == "ANY_VALUE"

    def test_values_matches_any_of_its_values_by_equality(self):
        target = guarding.values(5, 7)
        assert [target.matches(value) for value in (5, 7, 6)] == [True, True, False]

    def test_values_works_for_unhashable_targets(self):
        assert guarding.values([1], [2]).matches([2]) is True

    def test_values_requires_at_least_one(self):
        with pytest.raises(ValueError, match="^guarding.values requires at least one value$"):
            guarding.values()

    def test_reprs_read_like_what_was_written(self):
        assert repr(guarding(IsSuperUser, setting={"count": 5}).fields["count"]) == "5"
        assert repr(guarding.values(5, 7)) == "guarding.values(5, 7)"

    def test_guarding_values_is_the_same_marker_on_its_own(self):
        from isik.django.drf.permissions import guarding_values

        assert guarding.values is guarding_values
        assert guarding_values(5).matches(5) is True

    def test_a_custom_target_subclass_is_used_as_it_is(self, alice, bob):
        from isik.django.drf.permissions import GuardTarget

        class Above(GuardTarget):
            def __init__(self, floor):
                self.floor = floor

            def matches(self, incoming):
                return incoming > self.floor

        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), setting={"count": Above(10)}))
        assert patch(viewset, widget, bob, {"count": 9}).status_code == status.HTTP_200_OK
        assert patch(viewset, widget, bob, {"count": 11}).status_code == status.HTTP_400_BAD_REQUEST

    def test_other_than_matches_everything_but_its_values(self):
        target = guarding.other_than(5, 7)
        assert [target.matches(value) for value in (5, 7, 6, None)] == [False, False, True, True]
        assert repr(target) == "guarding.other_than(5, 7)"

    def test_other_than_requires_at_least_one(self):
        with pytest.raises(ValueError, match="^guarding.other_than requires at least one value$"):
            guarding.other_than()

    def test_matching_asks_its_predicate(self):
        def big(value):
            return value > 10

        target = guarding.matching(big)
        assert (target.matches(11), target.matches(10)) == (True, False)
        assert repr(target) == "guarding.matching('big')"

    def test_matching_coerces_to_bool(self):
        assert guarding.matching(lambda value: value).matches([1]) is True

    def test_matching_requires_a_callable(self):
        with pytest.raises(TypeError, match="^guarding.matching requires a callable$"):
            guarding.matching(5)

    def test_matching_repr_without_a_name(self):
        import functools

        target = guarding.matching(functools.partial(max, 0))
        assert repr(target).startswith("guarding.matching(functools.partial(")

    def test_the_marker_spellings_are_the_public_functions(self):
        from isik.django.drf.permissions import guarding_matching, guarding_other_than

        assert guarding.other_than is guarding_other_than
        assert guarding.matching is guarding_matching

    def test_other_than_and_matching_through_a_viewset(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        other_than = widget_viewset(guarding(is_owner("owner"), setting={"count": guarding.other_than(1, 2)}))
        assert patch(other_than, widget, bob, {"count": 2}).status_code == status.HTTP_200_OK
        assert patch(other_than, widget, bob, {"count": 3}).status_code == status.HTTP_400_BAD_REQUEST
        matching = widget_viewset(guarding(is_owner("owner"), setting={"count": guarding.matching(lambda n: n > 10)}))
        assert patch(matching, widget, bob, {"count": 11}).status_code == status.HTTP_400_BAD_REQUEST

    def test_the_base_target_is_abstract(self):
        from isik.django.drf.permissions import GuardTarget

        with pytest.raises(NotImplementedError):
            GuardTarget().matches(1)


class TestActionsGuard:
    def test_out_of_scope_actions_are_allowed_without_asking_the_predicate(self, rf, alice):
        guard = guarding(IsSuperUser, actions=["destroy"])()
        assert guard.has_permission(request_by(rf, alice), FakeView("retrieve")) is True
        assert guard.has_object_permission(request_by(rf, alice), FakeView("retrieve"), object()) is True

    def test_in_scope_request_level_predicate_refuses_at_request_time(self, rf, alice, root):
        guard = guarding(IsSuperUser, actions=["create"])()
        assert guard.has_permission(request_by(rf, alice), FakeView("create")) is False
        assert guard.has_permission(request_by(rf, root), FakeView("create")) is True

    def test_in_scope_object_predicate_defers_to_the_object(self, rf, alice, bob):
        widget = Widget.objects.create(name="bolt", owner=alice)
        guard = guarding(is_owner("owner"), actions=["destroy"])()
        assert guard.has_permission(request_by(rf, bob), FakeView("destroy")) is True
        assert guard.has_object_permission(request_by(rf, bob), FakeView("destroy"), widget) is False
        assert guard.has_object_permission(request_by(rf, alice), FakeView("destroy"), widget) is True

    def test_negating_an_object_predicate_works_where_drfs_own_not_refuses_everything(self, rf, alice, bob):
        widget = Widget.objects.create(name="bolt", owner=alice)
        # DRF's own NOT negates is_owner's default has_permission (True) and refuses up front.
        assert (~is_owner("owner"))().has_permission(request_by(rf, bob), FakeView("destroy")) is False

        guard = guarding(~is_owner("owner"), actions=["destroy"])()
        assert guard.has_permission(request_by(rf, bob), FakeView("destroy")) is True
        assert guard.has_object_permission(request_by(rf, bob), FakeView("destroy"), widget) is True
        assert guard.has_object_permission(request_by(rf, alice), FakeView("destroy"), widget) is False

    def test_negating_a_request_level_predicate_without_an_object(self, rf, alice, root):
        guard = guarding(~IsSuperUser, actions=["create"])()
        assert guard.has_permission(request_by(rf, root), FakeView("create")) is False
        assert guard.has_permission(request_by(rf, alice), FakeView("create")) is True

    def test_or_with_an_unknown_side_is_settled_by_a_true_side(self, rf, alice, root):
        guard = guarding(is_owner("owner") | IsSuperUser, actions=["create"])()
        assert guard.has_permission(request_by(rf, root), FakeView("create")) is True
        # unknown | False is unknown - allowed, since there is no row to hold it against
        assert guard.has_permission(request_by(rf, alice), FakeView("create")) is True

    def test_and_with_an_unknown_side_is_settled_by_a_false_side(self, rf, alice, root):
        guard = guarding(is_owner("owner") & IsSuperUser, actions=["create"])()
        assert guard.has_permission(request_by(rf, alice), FakeView("create")) is False
        assert guard.has_permission(request_by(rf, root), FakeView("create")) is True

    def test_known_sides_combine_normally_without_an_object(self, rf, alice, root):
        both = guarding(IsSuperUser & IsSuperUser, actions=["create"])()
        either = guarding(IsSuperUser | IsSuperUser, actions=["create"])()
        assert both.has_permission(request_by(rf, root), FakeView("create")) is True
        assert either.has_permission(request_by(rf, alice), FakeView("create")) is False

    def test_or_with_an_object_combines_both_halves_of_each_side(self, rf, alice, bob, root):
        widget = Widget.objects.create(name="bolt", owner=alice)
        guard = guarding(is_owner("owner") | IsSuperUser, actions=["destroy"])()
        assert guard.has_object_permission(request_by(rf, alice), FakeView("destroy"), widget) is True
        assert guard.has_object_permission(request_by(rf, root), FakeView("destroy"), widget) is True
        assert guard.has_object_permission(request_by(rf, bob), FakeView("destroy"), widget) is False

    def test_and_with_an_object(self, rf, alice, root):
        widget = Widget.objects.create(name="bolt", owner=alice)
        guard = guarding(is_owner("owner") & IsSuperUser, actions=["destroy"])()
        assert guard.has_object_permission(request_by(rf, alice), FakeView("destroy"), widget) is False
        widget.owner = root
        assert guard.has_object_permission(request_by(rf, root), FakeView("destroy"), widget) is True

    def test_a_request_level_leaf_is_still_asked_its_has_permission_with_an_object(self, rf, alice):
        guard = guarding(IsSuperUser, actions=["destroy"])()
        assert guard.has_object_permission(request_by(rf, alice), FakeView("destroy"), object()) is False

    def test_requires_a_view_with_an_action(self, rf, alice):
        guard = guarding(IsSuperUser, actions=["destroy"])()
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^IsSuperUserForDestroy needs a viewset - object has no action\.$",
        ):
            guard.has_permission(request_by(rf, alice), view=object())

    def test_and_with_the_object_level_side_on_the_right(self, rf, root, alice):
        widget = Widget.objects.create(name="bolt", owner=alice)
        guard = guarding(IsSuperUser & is_owner("owner"), actions=["destroy"])()
        assert guard.has_object_permission(request_by(rf, root), FakeView("destroy"), widget) is False

    @pytest.mark.parametrize(
        "predicate",
        [
            lambda: ~~RecordsView,
            lambda: RecordsView & RecordsView,
            lambda: RecordsView | ~RecordsView,
        ],
    )
    def test_every_operator_forwards_the_view_and_object_to_both_sides(self, rf, alice, seen, predicate):
        view = FakeView("destroy")
        obj = object()
        guard = guarding(predicate(), actions=["destroy"])()
        guard.has_permission(request_by(rf, alice), view)
        guard.has_object_permission(request_by(rf, alice), view, obj)
        permission_calls = [call for call in seen if call[0] == "permission"]
        object_calls = [call for call in seen if call[0] == "object"]
        assert permission_calls and all(call == ("permission", view) for call in permission_calls)
        assert object_calls and all(call == ("object", view, obj) for call in object_calls)
        # without an object, each leaf is asked once; with one, each is asked both halves
        assert len(permission_calls) == 2 * len(object_calls)


class TestFieldsGuardAtRequestTime:
    def test_allows_at_request_time_on_a_view_that_runs_field_guards(self, rf, alice):
        guard = guarding(IsSuperUser, fields=["count"])()
        assert guard.has_permission(request_by(rf, alice), FakeView("create", runs_field_guards=True)) is True
        assert guard.has_object_permission(request_by(rf, alice), FakeView("update"), object()) is True

    def test_fails_closed_on_a_view_that_does_not_run_field_guards(self, rf, alice):
        guard = guarding(IsSuperUser, fields=["count"])()
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^FakeView declares IsSuperUserForCount but doesn't run field guards - "
            r"add GuardedFieldsMixin \(part of BaseModelViewSet\)\.$",
        ):
            guard.has_permission(request_by(rf, alice), FakeView("create"))


class TestGuardedFieldsMixin:
    def test_is_part_of_base_model_viewset(self):
        assert issubclass(BaseModelViewSet, GuardedFieldsMixin)

    def test_changing_an_unguarded_field_is_allowed(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), fields=["count"]))
        response = patch(viewset, widget, bob, {"name": "nut"})
        assert response.status_code == status.HTTP_200_OK
        widget.refresh_from_db()
        assert widget.name == "nut"

    def test_changing_a_guarded_field_is_refused_as_a_field_error(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), fields=["count"]))
        response = patch(viewset, widget, bob, {"name": "nut", "count": 2})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"count": ["You may not change this field."]}
        assert response.data["count"][0].code == "permission_denied"
        widget.refresh_from_db()
        assert (widget.name, widget.count) == ("bolt", 1)

    def test_echoing_a_guarded_field_back_unchanged_is_not_a_change(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), fields=["count"]))
        response = patch(viewset, widget, bob, {"name": "nut", "count": 1})
        assert response.status_code == status.HTTP_200_OK

    def test_the_owner_may_change_it(self, alice):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), fields=["count"]))
        response = patch(viewset, widget, alice, {"count": 2})
        assert response.status_code == status.HTTP_200_OK
        widget.refresh_from_db()
        assert widget.count == 2

    def test_a_full_update_goes_through_the_same_check(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), fields=["count"]))
        request = APIRequestFactory().put(f"/widgets/{widget.pk}/", {"name": "nut", "count": 2}, format="json")
        force_authenticate(request, user=bob)
        response = viewset.as_view({"put": "update"})(request, pk=widget.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.data) == {"count"}

    def test_compares_the_typed_value_not_the_raw_payload(self, alice, bob):
        # form-encoded, "1" arrives as a string - only the validated int matches the stored value
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), fields=["count"]))
        response = patch(viewset, widget, bob, {"name": "nut", "count": "1"}, format="multipart")
        assert response.status_code == status.HTTP_200_OK

    def test_a_setting_scope_only_guards_its_target_value(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), setting={"count": 5}))
        assert patch(viewset, widget, bob, {"count": 3}).status_code == status.HTTP_200_OK
        response = patch(viewset, widget, bob, {"count": "5"}, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.data) == {"count"}

    def test_a_values_target_guards_each_of_its_values(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), setting={"count": guarding.values(5, 7)}))
        assert patch(viewset, widget, bob, {"count": 3}).status_code == status.HTTP_200_OK
        assert patch(viewset, widget, bob, {"count": 7}).status_code == status.HTTP_400_BAD_REQUEST
        assert patch(viewset, widget, bob, {"count": "5"}, format="multipart").status_code == 400

    def test_a_setting_scope_setting_the_target_it_already_holds_is_not_a_change(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=5, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), setting={"count": 5}))
        assert patch(viewset, widget, bob, {"count": 5}).status_code == status.HTTP_200_OK

    def test_every_refusing_guard_reports_its_own_fields(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(
            guarding(is_owner("owner"), fields=["count"]),
            guarding(IsSuperUser, fields=["name", "count"], message="superusers only"),
        )
        response = patch(viewset, widget, bob, {"name": "nut", "count": 2})
        assert response.data == {
            "count": ["You may not change this field.", "superusers only"],
            "name": ["superusers only"],
        }

    def test_a_guard_that_allows_reports_nothing(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(
            guarding(is_owner("owner"), fields=["count"]),
            guarding(user_property(attribute="is_active"), fields=["count"]),
        )
        response = patch(viewset, widget, bob, {"count": 2})
        assert response.data == {"count": ["You may not change this field."]}

    def test_a_negated_object_predicate_on_a_field(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(~is_owner("owner"), fields=["count"]))
        assert patch(viewset, widget, alice, {"count": 2}).status_code == status.HTTP_400_BAD_REQUEST
        assert patch(viewset, widget, bob, {"count": 2}).status_code == status.HTTP_200_OK

    def test_create_with_an_object_predicate_is_allowed(self, bob):
        # nothing to be the owner of yet
        viewset = widget_viewset(guarding(is_owner("owner"), fields=["count"]))
        assert post(viewset, bob, {"name": "nut", "count": 2}).status_code == status.HTTP_201_CREATED

    def test_create_with_a_request_level_predicate_is_checked(self, bob):
        viewset = widget_viewset(guarding(IsSuperUser, fields=["count"]))
        response = post(viewset, bob, {"name": "nut", "count": 2})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"count": ["You may not change this field."]}
        assert not Widget.objects.exists()

    def test_create_without_the_guarded_field_is_allowed(self, bob):
        viewset = widget_viewset(guarding(IsSuperUser, fields=["count"]))
        assert post(viewset, bob, {"name": "nut"}).status_code == status.HTTP_201_CREATED

    def test_a_read_only_guarded_field_is_never_written_so_never_refused(self, alice, bob):
        class ReadOnlyCountSerializer(WidgetSerializer):
            class Meta(WidgetSerializer.Meta):
                read_only_fields = ["id", "count"]

        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(IsSuperUser, fields=["count"]), serializer=ReadOnlyCountSerializer)
        assert patch(viewset, widget, bob, {"count": 2}).status_code == status.HTTP_200_OK

    def test_a_guarded_field_the_serializer_does_not_have_fails_loudly(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(IsSuperUser, fields=["cuont"]))
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^IsSuperUserForCuont guards 'cuont', which no serializer of GuardedWidgetViewSet has a field",
        ):
            patch(viewset, widget, bob, {"count": 2})

    def test_action_guards_are_not_field_guards(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(IsSuperUser, actions=["destroy"]))
        assert patch(viewset, widget, bob, {"count": 2}).status_code == status.HTTP_200_OK

    def test_an_actions_guard_refuses_with_a_403(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), actions=["destroy"], message="owner only"))
        request = APIRequestFactory().delete(f"/widgets/{widget.pk}/")
        force_authenticate(request, user=bob)
        response = viewset.as_view({"delete": "destroy"})(request, pk=widget.pk)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data == {"detail": "owner only"}
        assert Widget.objects.filter(pk=widget.pk).exists()

    def test_a_guard_nested_in_a_composition_fails_at_class_definition(self):
        # An already-composed holder on the left builds the composition before the guard's own
        # metaclass is asked - so only the viewset can catch it.
        guard = guarding(IsSuperUser, fields=["count"])
        with pytest.raises(ImproperlyConfigured, match="must be its own permission_classes entry"):
            widget_viewset((IsAuthenticated & IsSuperUser) | guard)
        with pytest.raises(ImproperlyConfigured, match="must be its own permission_classes entry"):
            widget_viewset(~((IsAuthenticated & IsSuperUser) & guard))
        with pytest.raises(ImproperlyConfigured, match="must be its own permission_classes entry"):
            widget_viewset(((IsAuthenticated & IsSuperUser) | guard) & IsAuthenticated)

    def test_the_mixin_forwards_itself_as_the_view(self, alice, bob, seen):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(RecordsView, fields=["count"]))
        patch(viewset, widget, bob, {"count": 2})
        assert seen and all(isinstance(call[1], viewset) for call in seen)

    @pytest.mark.parametrize(
        ("serializer_read_only", "scope", "payload"),
        [
            # each skip reason on the first field, then a real change to the second - skipping must
            # move on to the next field, not stop looking
            (["id", "count"], {"fields": ["count", "name"]}, {"count": 2, "name": "nut"}),
            (["id"], {"fields": ["count", "name"]}, {"name": "nut"}),
            (["id"], {"setting": {"count": 5, "name": "nut"}}, {"count": 3, "name": "nut"}),
            (["id"], {"fields": ["count", "name"]}, {"count": 1, "name": "nut"}),
        ],
    )
    def test_a_skipped_field_does_not_hide_a_later_changed_one(self, alice, bob, serializer_read_only, scope, payload):
        class Serializer(WidgetSerializer):
            class Meta(WidgetSerializer.Meta):
                read_only_fields = serializer_read_only

        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = widget_viewset(guarding(is_owner("owner"), **scope), serializer=Serializer)
        response = patch(viewset, widget, bob, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.data) == {"name"}

    def test_a_plain_composition_is_fine(self):
        widget_viewset(IsAuthenticated & ~IsSuperUser, guarding(IsSuperUser, fields=["count"]))

    def test_a_guard_on_a_viewset_without_the_mixin_fails_closed(self, alice, bob):
        widget = Widget.objects.create(name="bolt", count=1, owner=alice)
        viewset = type(
            "UnguardedWidgetViewSet",
            (ModelViewSet,),
            {
                "queryset": Widget.objects.all(),
                "serializer_class": WidgetSerializer,
                "permission_classes": [guarding(is_owner("owner"), fields=["count"])],
            },
        )
        with pytest.raises(ImproperlyConfigured, match="doesn't run field guards"):
            patch(viewset, widget, bob, {"count": 2})
        widget.refresh_from_db()
        assert widget.count == 1

    def test_forwards_to_super_after_checking(self):
        saved = []

        class RecordingBase:
            def perform_create(self, serializer):
                saved.append(("create", serializer))

            def perform_update(self, serializer):
                saved.append(("update", serializer))

        class RecordingViewSet(GuardedFieldsMixin, RecordingBase):
            def get_field_guards(self):
                return []

        class Serializer:
            instance = None
            fields = {}
            validated_data = {}

        serializer = Serializer()
        RecordingViewSet().perform_create(serializer)
        RecordingViewSet().perform_update(serializer)
        assert saved == [("create", serializer), ("update", serializer)]


class TestChangeDetectionHelpers:
    def test_dig_walks_dicts_and_attributes(self):
        class Owner:
            username = "alice"

        class Row:
            owner = Owner()

        assert _dig({"owner": {"username": "bob"}}, ["owner", "username"]) == "bob"
        assert _dig(Row(), ["owner", "username"]) == "alice"
        assert _dig({"owner": {}}, ["owner", "username"]) is _MISSING
        assert _dig(Row(), ["nothing"]) is _MISSING

    def test_dig_keeps_a_stored_none(self):
        assert _dig({"owner": None}, ["owner"]) is None

    def test_a_related_manager_compares_its_rows(self):
        class Manager:
            def all(self):
                return [1, 2]

        assert _changed(Manager(), [2, 1]) is False
        assert _changed(Manager(), [1]) is True

    def test_a_nested_payload_always_counts_as_a_change(self):
        assert _changed({"a": 1}, {"a": 1}) is True

    def test_plain_values_compare_by_equality(self):
        assert _changed(1, 1) is False
        assert _changed(1, 2) is True


class TestObjectProperty:
    def test_requires_exactly_one_of_property_or_attribute(self):
        with pytest.raises(ValueError, match="^object_property requires exactly one of property_ or attribute$"):
            object_property()
        with pytest.raises(ValueError):
            object_property(property_=property(lambda self: True), attribute="x")

    def test_reads_an_attribute_off_the_object(self, rf, alice):
        class Row:
            is_app = True

        permission = object_property(attribute="is_app")()
        assert permission.has_object_permission(request_by(rf, alice), None, Row()) is True
        Row.is_app = False
        assert permission.has_object_permission(request_by(rf, alice), None, Row()) is False

    def test_reads_a_property_off_the_object(self, rf, alice):
        class Row:
            @property
            def is_app(self):
                return 1

        permission = object_property(property_=Row.is_app)()
        assert permission.has_object_permission(request_by(rf, alice), None, Row()) is True

    def test_denies_instead_of_raising_when_the_object_lacks_it(self, rf, alice):
        permission = object_property(attribute="is_app")()
        assert permission.has_object_permission(request_by(rf, alice), None, object()) is False

    def test_has_nothing_to_say_without_an_object(self, rf, alice):
        assert object_property(attribute="is_app")().has_permission(request_by(rf, alice), None) is True

    def test_name_and_message(self):
        permission_cls = object_property(attribute="is_app")
        assert permission_cls.__name__ == "ObjectIsApp"
        assert permission_cls.message == "Object property is_app is False"

    def test_negated_inside_guarding(self, rf, alice):
        class AppAccount:
            is_app = True

        class Person:
            is_app = False

        guard = guarding(~object_property(attribute="is_app"), actions=["partial_update"])()
        view = FakeView("partial_update")
        assert guard.has_permission(request_by(rf, alice), view) is True
        assert guard.has_object_permission(request_by(rf, alice), view, AppAccount()) is False
        assert guard.has_object_permission(request_by(rf, alice), view, Person()) is True


class TestIsOwnerTraversal:
    def test_follows_a_dotted_owner_path(self, rf, alice, bob):
        alice.manager = bob
        widget = Widget.objects.create(name="bolt", owner=alice)
        permission = is_owner("owner.manager")()
        assert permission.has_object_permission(request_by(rf, bob), None, widget) is True
        assert permission.has_object_permission(request_by(rf, alice), None, widget) is False

    def test_a_path_broken_by_a_none_relation_is_not_the_owner(self, rf, alice):
        widget = Widget.objects.create(name="bolt", owner=None)
        assert is_owner("owner.manager")().has_object_permission(request_by(rf, alice), None, widget) is False

    def test_takes_a_callable_owner(self, rf, alice, bob):
        widget = Widget.objects.create(name="bolt", owner=alice)

        def owner_of(obj):
            return obj.owner

        assert is_owner(owner_of)().has_object_permission(request_by(rf, alice), None, widget) is True
        assert is_owner(owner_of)().has_object_permission(request_by(rf, bob), None, widget) is False
        assert is_owner(owner_of).__name__ == "IsOwnerByOwnerOf"

    def test_takes_a_callable_of(self, rf, alice, bob):
        bob.manager = alice
        widget = Widget.objects.create(name="bolt", owner=alice)
        permission_cls = is_owner("owner", of=lambda user: user.manager)
        assert permission_cls().has_object_permission(request_by(rf, bob), None, widget) is True
        assert permission_cls.__name__ == "IsOwnerByOwnerOfLambda"

    def test_a_callable_raising_attribute_error_is_not_the_owner(self, rf, alice):
        widget = Widget.objects.create(name="bolt", owner=None)
        permission = is_owner(lambda obj: obj.owner.manager)()
        assert permission.has_object_permission(request_by(rf, alice), None, widget) is False


class TestIsOwnerOf:
    def test_compares_against_an_attribute_of_the_user(self, rf, alice, bob):
        bob.manager = alice
        widget = Widget.objects.create(name="bolt", owner=alice)
        assert is_owner("owner", of="manager")().has_object_permission(request_by(rf, bob), None, widget) is True
        assert is_owner("owner", of="manager")().has_object_permission(request_by(rf, alice), None, widget) is False

    def test_follows_a_dotted_path(self, rf, alice, bob):
        bob.manager = alice
        alice.manager = bob
        widget = Widget.objects.create(name="bolt", owner=bob)
        permission = is_owner("owner", of="manager.manager")()
        assert permission.has_object_permission(request_by(rf, bob), None, widget) is True

    def test_denies_when_the_user_has_no_such_attribute(self, rf, alice):
        widget = Widget.objects.create(name="bolt", owner=alice)
        permission = is_owner("owner", of="organization")()
        assert permission.has_object_permission(request_by(rf, AnonymousUser()), None, widget) is False

    def test_denies_when_both_sides_are_none(self, rf, alice):
        widget = Widget.objects.create(name="bolt", owner=None)
        permission = is_owner("owner", of="manager")()
        assert permission.has_object_permission(request_by(rf, alice), None, widget) is False

    def test_a_callable_without_a_name_falls_back_to_its_type(self):
        import functools

        owner_of = functools.partial(getattr, "owner")
        assert is_owner(owner_of).__name__ == "IsOwnerByPartial"

    def test_name_includes_of(self):
        assert is_owner("tenant", of="organization").__name__ == ("IsOwnerByTenantOfOrganization")
