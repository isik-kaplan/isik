"""
Field guards against every kind of serializer field isik (and DRF) offers, on every write action.

The rule under test: a guard answers for what the write *changes*, however the value arrives - the
payload, a serializer default, or a HiddenField such as CurrentUserField. A field the write can't
reach (read-only, create-only on update, dropped by `?only=`/`?exclude=`) is never refused.
"""

import pytest
from rest_framework import serializers, status
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.viewsets import ModelViewSet

from isik.django.drf.permissions import IsSuperUser, guarding
from isik.django.drf.serializers.base import BaseModelSerializer
from isik.django.drf.utils.current_user import CurrentUserField
from isik.django.drf.utils.lazy_relations import LazyPrimaryKeyRelatedField
from isik.django.drf.viewsets import GuardedFieldsMixin
from isik.django.drf.viewsets.action_serializer_class import ActionSerializerClassMixin
from tests.testapp.models import EmailUser, Widget, WidgetProfile


pytestmark = pytest.mark.django_db

REFUSED, ALLOWED = status.HTTP_400_BAD_REQUEST, "allowed"


class MatrixSerializer(BaseModelSerializer):
    """The full stack - create-only, write-only, flattened one-to-one, conditional fields."""

    exempt_from_registry = True

    class Meta:
        model = Widget
        fields = ["id", "name", "count"]


class Plain(MatrixSerializer):
    pass


class Renamed(MatrixSerializer):
    amount = serializers.IntegerField(source="count", required=False)

    class Meta(MatrixSerializer.Meta):
        fields = ["id", "name", "amount"]


class WriteOnly(MatrixSerializer):
    class Meta(MatrixSerializer.Meta):
        write_only_fields = ["count"]


class CreateOnly(MatrixSerializer):
    class Meta(MatrixSerializer.Meta):
        create_only_fields = ["count"]


class ReadOnly(MatrixSerializer):
    class Meta(MatrixSerializer.Meta):
        read_only_fields = ["id", "count"]


class Defaulted(MatrixSerializer):
    count = serializers.IntegerField(default=5)


class OwnerPk(MatrixSerializer):
    class Meta(MatrixSerializer.Meta):
        fields = ["id", "name", "owner"]


class LazyOwner(MatrixSerializer):
    owner = LazyPrimaryKeyRelatedField(queryset_func=lambda: EmailUser.objects.all(), required=False, allow_null=True)

    class Meta(MatrixSerializer.Meta):
        fields = ["id", "name", "owner"]


class CurrentOwner(MatrixSerializer):
    owner = CurrentUserField()

    class Meta(MatrixSerializer.Meta):
        fields = ["id", "name", "owner"]


class CreatedBy(MatrixSerializer):
    owner = CurrentUserField(create_only=True)

    class Meta(MatrixSerializer.Meta):
        fields = ["id", "name", "owner"]


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = WidgetProfile
        fields = ["bio"]


class Flattened(MatrixSerializer):
    class Meta(MatrixSerializer.Meta):
        fields = ["id", "name"]
        flattened_one_to_one_fields = {"profile": ProfileSerializer}


# field kind -> (serializer, guarded field, payload value equal to what's stored, a different one).
# Values are callables of (alice, bob) since some of them are users. The stored widget belongs to
# alice, has count=1 and a profile whose bio is "old"; bob does the writing.
KINDS = {
    "plain": (Plain, "count", lambda a, b: 1, lambda a, b: 2),
    "renamed source": (Renamed, "amount", lambda a, b: 1, lambda a, b: 2),
    "write-only": (WriteOnly, "count", lambda a, b: 1, lambda a, b: 2),
    "create-only": (CreateOnly, "count", lambda a, b: 1, lambda a, b: 2),
    "read-only": (ReadOnly, "count", lambda a, b: 1, lambda a, b: 2),
    "serializer default": (Defaulted, "count", lambda a, b: 1, lambda a, b: 2),
    "foreign key": (OwnerPk, "owner", lambda a, b: a.pk, lambda a, b: b.pk),
    "lazy foreign key": (LazyOwner, "owner", lambda a, b: a.pk, lambda a, b: b.pk),
    "current user": (CurrentOwner, "owner", lambda a, b: a.pk, lambda a, b: b.pk),
    "current user, create-only": (CreatedBy, "owner", lambda a, b: a.pk, lambda a, b: b.pk),
    "flattened one-to-one": (Flattened, "bio", lambda a, b: "old", lambda a, b: "new"),
}

# (kind, scenario) -> outcome, for each action. "sent" on create means the client sent a value;
# "changed"/"unchanged"/"omitted" on updates compare the payload to the stored row.
CREATE = {
    ("plain", "sent"): REFUSED,
    ("plain", "omitted"): ALLOWED,
    ("renamed source", "sent"): REFUSED,
    ("renamed source", "omitted"): ALLOWED,
    ("write-only", "sent"): REFUSED,
    ("write-only", "omitted"): ALLOWED,
    ("create-only", "sent"): REFUSED,
    ("create-only", "omitted"): ALLOWED,
    ("read-only", "sent"): ALLOWED,  # the client's value is dropped, nothing is written
    ("read-only", "omitted"): ALLOWED,
    ("serializer default", "sent"): REFUSED,
    ("serializer default", "omitted"): REFUSED,  # the default is still a value this write supplies
    ("foreign key", "sent"): REFUSED,
    ("foreign key", "omitted"): ALLOWED,
    ("lazy foreign key", "sent"): REFUSED,
    ("lazy foreign key", "omitted"): ALLOWED,
    ("current user", "sent"): REFUSED,  # a HiddenField ignores the payload but always supplies itself
    ("current user", "omitted"): REFUSED,
    ("current user, create-only", "sent"): REFUSED,
    ("current user, create-only", "omitted"): REFUSED,
    ("flattened one-to-one", "sent"): REFUSED,
    ("flattened one-to-one", "omitted"): ALLOWED,
}

UPDATE = {
    ("plain", "changed"): REFUSED,
    ("plain", "unchanged"): ALLOWED,
    ("plain", "omitted"): ALLOWED,
    ("renamed source", "changed"): REFUSED,
    ("renamed source", "unchanged"): ALLOWED,
    ("renamed source", "omitted"): ALLOWED,
    ("write-only", "changed"): REFUSED,
    ("write-only", "unchanged"): ALLOWED,
    ("write-only", "omitted"): ALLOWED,
    ("create-only", "changed"): ALLOWED,  # read-only once the row exists
    ("create-only", "unchanged"): ALLOWED,
    ("create-only", "omitted"): ALLOWED,
    ("read-only", "changed"): ALLOWED,
    ("read-only", "unchanged"): ALLOWED,
    ("read-only", "omitted"): ALLOWED,
    ("serializer default", "changed"): REFUSED,
    ("serializer default", "unchanged"): ALLOWED,
    ("serializer default", "omitted"): ALLOWED,  # partial updates don't apply defaults
    ("foreign key", "changed"): REFUSED,
    ("foreign key", "unchanged"): ALLOWED,
    ("foreign key", "omitted"): ALLOWED,
    ("lazy foreign key", "changed"): REFUSED,
    ("lazy foreign key", "unchanged"): ALLOWED,
    ("lazy foreign key", "omitted"): ALLOWED,
    # partial updates skip defaults, a HiddenField's included - so PATCH never touches it; a full
    # update (see test_update) reassigns the row to bob, which is a real change and refused
    ("current user", "changed"): ALLOWED,
    ("current user", "unchanged"): ALLOWED,
    ("current user", "omitted"): ALLOWED,
    ("current user, create-only", "changed"): ALLOWED,  # keeps alice
    ("current user, create-only", "unchanged"): ALLOWED,
    ("current user, create-only", "omitted"): ALLOWED,
    ("flattened one-to-one", "changed"): REFUSED,
    ("flattened one-to-one", "unchanged"): ALLOWED,
    ("flattened one-to-one", "omitted"): ALLOWED,
}


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="password")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user(username="bob", email="bob@example.com", password="password")


@pytest.fixture
def root(django_user_model):
    return django_user_model.objects.create_superuser(username="root", email="root@example.com", password="password")


@pytest.fixture
def widget(alice):
    widget = Widget.objects.create(name="bolt", count=1, owner=alice)
    WidgetProfile.objects.create(widget=widget, bio="old")
    return widget


def viewset_for(serializer_cls, field, **attrs):
    return type(
        f"{serializer_cls.__name__}ViewSet",
        (GuardedFieldsMixin, ModelViewSet),
        {
            "queryset": Widget.objects.all(),
            "serializer_class": serializer_cls,
            "permission_classes": [guarding(IsSuperUser, fields=[field])],
            **attrs,
        },
    )


def write(viewset, user, method, action, data, pk=None, query=""):
    factory = APIRequestFactory()
    path = f"/widgets/{pk}/{query}" if pk else f"/widgets/{query}"
    request = getattr(factory, method)(path, data, format="json")
    force_authenticate(request, user=user)
    kwargs = {"pk": pk} if pk else {}
    return viewset.as_view({method: action})(request, **kwargs)


def outcome(response):
    if response.status_code == REFUSED:
        assert response.data.get(next(iter(response.data))) == ["You may not change this field."]
        return REFUSED
    assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), response.data
    return ALLOWED


@pytest.mark.parametrize(("kind", "scenario"), list(CREATE))
def test_create(kind, scenario, alice, bob):
    serializer_cls, field, same, _changed = KINDS[kind]
    data = {"name": "nut"}
    if scenario == "sent":
        data[field] = same(alice, bob)
    assert outcome(write(viewset_for(serializer_cls, field), bob, "post", "create", data)) == CREATE[(kind, scenario)]


@pytest.mark.parametrize("action", ["partial_update", "update"])
@pytest.mark.parametrize(("kind", "scenario"), list(UPDATE))
def test_update(kind, scenario, action, alice, bob, widget):
    serializer_cls, field, same, changed = KINDS[kind]
    data = {"name": "nut"}
    if scenario == "changed":
        data[field] = changed(alice, bob)
    elif scenario == "unchanged":
        data[field] = same(alice, bob)
    method = "patch" if action == "partial_update" else "put"
    response = write(viewset_for(serializer_cls, field), bob, method, action, data, pk=widget.pk)
    expected = UPDATE[(kind, scenario)]
    if action == "update" and kind in ("serializer default", "current user") and scenario == "omitted":
        expected = REFUSED  # a full update applies the default (5 / bob), which changes the stored value
    if action == "update" and kind == "current user":
        expected = REFUSED  # every full update by bob reassigns alice's row to bob
    assert outcome(response) == expected


@pytest.mark.parametrize(("kind", "scenario"), [key for key, value in UPDATE.items() if value == REFUSED])
def test_a_permitted_caller_is_never_refused(kind, scenario, alice, bob, root, widget):
    serializer_cls, field, same, changed = KINDS[kind]
    data = {"name": "nut", field: (changed if scenario == "changed" else same)(alice, bob)}
    response = write(viewset_for(serializer_cls, field), root, "patch", "partial_update", data, pk=widget.pk)
    assert outcome(response) == ALLOWED


def test_a_refused_write_changes_nothing(alice, bob, widget):
    viewset = viewset_for(Flattened, "bio")
    assert (
        outcome(write(viewset, bob, "patch", "partial_update", {"name": "nut", "bio": "new"}, pk=widget.pk)) == REFUSED
    )
    widget.refresh_from_db()
    assert (widget.name, widget.profile.bio) == ("bolt", "old")


def test_a_flattened_field_with_no_related_row_yet_counts_as_a_change(alice, bob):
    widget = Widget.objects.create(name="bolt", count=1, owner=alice)
    response = write(viewset_for(Flattened, "bio"), bob, "patch", "partial_update", {"bio": "new"}, pk=widget.pk)
    assert outcome(response) == REFUSED


class TestConditionalFields:
    @pytest.mark.parametrize("query", ["?exclude=count", "?only=name"])
    def test_a_field_dropped_by_the_query_is_not_written_so_not_refused(self, query, bob, widget):
        response = write(viewset_for(Plain, "count"), bob, "patch", "partial_update", {"count": 2}, widget.pk, query)
        assert outcome(response) == ALLOWED
        widget.refresh_from_db()
        assert widget.count == 1

    def test_a_dropped_field_does_not_hide_a_later_guarded_one(self, bob, widget):
        class DropsFirst(MatrixSerializer):
            pass

        viewset = type(
            "DropsFirstViewSet",
            (GuardedFieldsMixin, ModelViewSet),
            {
                "queryset": Widget.objects.all(),
                "serializer_class": DropsFirst,
                "permission_classes": [guarding(IsSuperUser, fields=["count", "name"])],
            },
        )
        response = write(viewset, bob, "patch", "partial_update", {"name": "nut"}, widget.pk, "?exclude=count")
        assert outcome(response) == REFUSED
        assert set(response.data) == {"name"}

    def test_a_field_kept_by_the_query_is_still_guarded(self, bob, widget):
        viewset = viewset_for(Plain, "count")
        response = write(viewset, bob, "patch", "partial_update", {"count": 2}, widget.pk, "?only=count")
        assert outcome(response) == REFUSED


class ForActions(MatrixSerializer):
    """Not shared with any other test viewset, so their guards can't disagree with these."""


class ForTypos(MatrixSerializer):
    pass


class TestPerActionSerializers:
    def test_an_action_whose_serializer_lacks_the_field_just_skips_it(self, bob):
        class NameOnly(MatrixSerializer):
            class Meta(MatrixSerializer.Meta):
                fields = ["id", "name"]

        viewset = type(
            "SkipsMissing",
            (ActionSerializerClassMixin, viewset_for(ForActions, "count")),
            {"serializer_class_action_map": {"create": NameOnly}},
        )
        assert outcome(write(viewset, bob, "post", "create", {"name": "nut", "count": 2})) == ALLOWED

    def test_the_typo_check_looks_at_the_action_map_too(self, bob):
        class CountAsAmount(MatrixSerializer):
            amount = serializers.IntegerField(source="count", required=False)

            class Meta(MatrixSerializer.Meta):
                fields = ["id", "name", "amount"]

        # "amount" exists only on the create serializer - an update through ForActions skips it
        viewset = type(
            "ChecksTheMap",
            (ActionSerializerClassMixin, viewset_for(ForTypos, "amount")),
            {"serializer_class_action_map": {"create": CountAsAmount}},
        )
        widget = Widget.objects.create(name="bolt", count=1)
        assert outcome(write(viewset, bob, "patch", "partial_update", {"count": 2}, pk=widget.pk)) == ALLOWED
        assert outcome(write(viewset, bob, "post", "create", {"name": "nut", "amount": 2})) == REFUSED

    def test_a_name_no_declared_serializer_has_is_a_mistake(self, bob, widget):
        from django.core.exceptions import ImproperlyConfigured

        class ForTypoOnly(MatrixSerializer):
            pass

        with pytest.raises(ImproperlyConfigured, match="which no serializer of ForTypoOnlyViewSet has a field named"):
            write(viewset_for(ForTypoOnly, "cuont"), bob, "patch", "partial_update", {"count": 2}, pk=widget.pk)


class TestSharedSerializers:
    """Two viewsets over one serializer must agree on its guarded fields, or say they differ on purpose."""

    @staticmethod
    def define(name, serializer_cls, *guarded, **attrs):
        permission_classes = [guarding(IsSuperUser, fields=list(guarded))] if guarded else []
        return type(
            name,
            (GuardedFieldsMixin, ModelViewSet),
            {
                "queryset": Widget.objects.all(),
                "serializer_class": serializer_cls,
                "permission_classes": permission_classes,
                **attrs,
            },
        )

    def test_a_second_viewset_leaving_a_guarded_field_open_fails(self):
        from django.core.exceptions import ImproperlyConfigured

        class Shared(MatrixSerializer):
            pass

        self.define("TenantWidgets", Shared, "count")
        with pytest.raises(
            ImproperlyConfigured,
            match=r"^TenantWidgets guards \['count'\] on Shared, but PlatformWidgets uses the same serializer "
            r"without guarding them - guard them there too, or list them in PlatformWidgets\.unguarded_fields",
        ):
            self.define("PlatformWidgets", Shared)

    def test_the_lenient_one_defined_first_fails_too(self):
        from django.core.exceptions import ImproperlyConfigured

        class SharedFirst(MatrixSerializer):
            pass

        self.define("OpenFirst", SharedFirst)
        with pytest.raises(
            ImproperlyConfigured, match=r"^GuardedSecond guards \['count'\] on SharedFirst, but OpenFirst"
        ):
            self.define("GuardedSecond", SharedFirst, "count")

    def test_unguarded_fields_declares_the_difference_intended(self):
        class SharedOnPurpose(MatrixSerializer):
            pass

        self.define("StrictWidgets", SharedOnPurpose, "count")
        self.define("StaffWidgets", SharedOnPurpose, unguarded_fields=["count"])

        # and in the other order
        class SharedOnPurposeToo(MatrixSerializer):
            pass

        self.define("StaffFirst", SharedOnPurposeToo, unguarded_fields=["count"])
        self.define("StrictSecond", SharedOnPurposeToo, "count")

    def test_agreeing_viewsets_are_fine(self):
        class SharedAgreed(MatrixSerializer):
            pass

        self.define("OneWay", SharedAgreed, "count", "name")
        self.define("OtherWay", SharedAgreed, "name", "count")

    def test_redefining_the_same_class_replaces_it(self):
        class SharedRedefined(MatrixSerializer):
            pass

        self.define("Redefined", SharedRedefined, "count")
        self.define("Redefined", SharedRedefined)  # same module and name - a re-run, not a second viewset

    def test_a_redefined_class_is_still_compared_with_the_others(self):
        from django.core.exceptions import ImproperlyConfigured

        class SharedAfterRedefinition(MatrixSerializer):
            pass

        self.define("RedefinedLater", SharedAfterRedefinition, "count")
        self.define("StaysGuarded", SharedAfterRedefinition, "count")
        # its own old entry comes first and is skipped - the check must go on to StaysGuarded
        with pytest.raises(ImproperlyConfigured, match="StaysGuarded guards"):
            self.define("RedefinedLater", SharedAfterRedefinition)

    def test_the_action_map_is_compared_too(self):
        from django.core.exceptions import ImproperlyConfigured

        class MappedShared(MatrixSerializer):
            pass

        class Other(MatrixSerializer):
            pass

        self.define("UsesItDirectly", MappedShared, "count")
        with pytest.raises(ImproperlyConfigured, match="UsesItViaTheMap uses the same serializer"):
            type(
                "UsesItViaTheMap",
                (ActionSerializerClassMixin, self.define("MapBase", Other)),
                {
                    "serializer_class_action_map": {"create": MappedShared},
                },
            )

    def test_an_abstract_base_without_a_serializer_is_skipped(self):
        self.define("NoSerializerYet", None, "count")
