"""redeclare() and if_unset() - a library's field, declared again as the project's own."""

import types
import uuid

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AbstractUser, Group, PermissionsMixin
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.common.db import IfUnset, if_unset, redeclare
from isik.django.apps.common.db.documentation import _from_a_library, documentation_problems


@pytest.fixture(autouse=True)
def isolated():
    with isolate_apps("tests.testapp"):
        yield


def model(fields, bases=(models.Model,), abstract=False):
    meta = type("Meta", (), {"app_label": "testapp", "abstract": abstract})
    attrs = {"__module__": __name__, "Meta": meta, **fields}
    return type(f"Redeclaring{uuid.uuid4().hex[:8]}", bases, attrs)


def definition(field):
    return field.deconstruct()[1:]


class TestTheFieldItBuilds:
    def test_is_the_librarys_with_only_the_changes_made(self):
        _, path, args, kwargs = AbstractUser.username.field.deconstruct()

        built = redeclare(AbstractUser.username, max_length=200, db_comment="What the person signs in as.")

        assert definition(built) == (
            path,
            args,
            {**kwargs, "max_length": 200, "db_comment": "What the person signs in as."},
        )
        assert built.unique
        assert built.validators[0] is AbstractUser.username.field.validators[0]  # its username validator
        assert built.validators[1].limit_value == 200

    def test_a_plain_value_replaces_the_librarys(self):
        built = redeclare(AbstractUser.is_staff, help_text="Whether the person can sign in to the admin.")

        assert built.help_text == "Whether the person can sign in to the admin."

    def test_if_unset_fills_what_the_library_left_unset(self):
        built = redeclare(AbstractUser.first_name, help_text=if_unset("What the person is called first."))

        assert built.help_text == "What the person is called first."

    def test_if_unset_keeps_what_the_library_set(self):
        built = redeclare(AbstractUser.is_staff, help_text=if_unset("Mine."), db_comment="Whether staff.")

        assert built.help_text == AbstractUser.is_staff.field.help_text
        assert built.db_comment == "Whether staff."

    def test_is_a_new_field(self):
        built = redeclare(AbstractUser.first_name, db_comment="What the person is called first.")

        assert built is not AbstractUser.first_name.field
        assert built.creation_counter != AbstractUser.first_name.field.creation_counter

    def test_takes_the_field_itself_too(self):
        built = redeclare(AbstractUser.email.field, db_comment="Where the person is written to.")

        assert built.db_comment == "Where the person is written to."

    @pytest.mark.parametrize(
        ("declared_by", "name"),
        [(AbstractBaseUser, "password"), (AbstractBaseUser, "last_login"), (PermissionsMixin, "is_superuser")],
    )
    def test_reaches_a_field_declared_further_up(self, declared_by, name):
        built = redeclare(getattr(AbstractUser, name), db_comment="Said here.")

        assert definition(built)[2] == {**definition(getattr(declared_by, name).field)[2], "db_comment": "Said here."}

    def test_a_many_to_many(self):
        built = redeclare(AbstractUser.groups, help_text="The person's groups.")

        assert isinstance(built, models.ManyToManyField)
        assert definition(built)[2]["to"] == "auth.group"

    def test_a_primary_key(self):
        base = model({"id": models.BigAutoField(primary_key=True)}, abstract=True)

        built = redeclare(base.id, help_text="Its key.", db_comment="Its key.")

        assert isinstance(built, models.BigAutoField) and built.primary_key

    def test_a_key_to_a_swappable_model(self):
        base = model(
            {"owner": models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")},
            abstract=True,
        )

        built = redeclare(base.owner, help_text="Who owns it.")

        kwargs = definition(built)[2]
        assert kwargs["to"].setting_name == "AUTH_USER_MODEL"
        assert kwargs["on_delete"] is models.PROTECT

    def test_a_key_to_a_swappable_model_while_models_are_declared(self, monkeypatch):
        base = model(
            {"owner": models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")},
            abstract=True,
        )
        apps.get_swappable_settings_name.cache_clear()
        monkeypatch.setattr(apps, "models_ready", False)

        built = redeclare(base.owner, help_text="Who owns it.")

        monkeypatch.undo()
        assert built.swappable and base.owner.field.swappable
        assert definition(built)[2]["to"].setting_name == "AUTH_USER_MODEL"

    def test_a_key_that_isnt_swappable_stays_so(self):
        base = model(
            {
                "owner": models.ForeignKey(
                    settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", swappable=False
                )
            },
            abstract=True,
        )

        built = redeclare(base.owner, help_text="Who owns it.")

        assert built.swappable is False
        assert not hasattr(definition(built)[2]["to"], "setting_name")

    def test_a_custom_fields_positional_arguments_are_kept(self):
        class TaggedField(models.CharField):
            def __init__(self, tag, *args, **kwargs):
                self.tag = tag
                super().__init__(*args, **kwargs)

            def deconstruct(self):
                name, path, args, kwargs = super().deconstruct()
                return name, path, [self.tag, *args], kwargs

        base = model({"code": TaggedField("sku", max_length=8)}, abstract=True)

        built = redeclare(base.code, help_text="Its code.")

        assert (built.tag, built.max_length, built.help_text) == ("sku", 8, "Its code.")

    def test_an_argument_the_field_doesnt_take_fails_where_its_written(self):
        with pytest.raises(TypeError):
            redeclare(AbstractUser.is_staff, max_digits=3)


class TestDeclaredOnAModel:
    def test_is_the_models_own_with_the_change(self):
        made = model(
            {
                "first_name": redeclare(
                    AbstractUser.first_name, help_text="What the person is called first.", db_comment="First name."
                ),
                "groups": models.ManyToManyField("auth.Group", related_name="+"),
                "user_permissions": models.ManyToManyField("auth.Permission", related_name="+"),
            },
            bases=(AbstractUser,),
        )
        field = made._meta.get_field("first_name")

        assert field.help_text == "What the person is called first."
        assert field.max_length == AbstractUser.first_name.field.max_length
        assert not _from_a_library(made, field)
        config = types.SimpleNamespace(get_models=lambda: [made])
        described = documentation_problems([config], inherited_from_libraries=False)
        assert not any(".first_name " in problem for problem in described)


class TestRefused:
    def test_a_concrete_models_field(self):
        with pytest.raises(ImproperlyConfigured) as refused:
            redeclare(Group.name, help_text="The group's name.")

        assert str(refused.value) == (
            "redeclare(Group.name): Group is a concrete model, whose own table holds name - redeclare() takes a "
            "field an abstract model declares."
        )

    def test_changing_nothing(self):
        with pytest.raises(ImproperlyConfigured) as refused:
            redeclare(AbstractUser.first_name)

        assert str(refused.value) == (
            "redeclare(AbstractUser.first_name) changes nothing - inheriting it declares the same field."
        )

    def test_what_isnt_a_field(self):
        with pytest.raises(TypeError) as refused:
            redeclare("first_name", help_text="x")

        assert str(refused.value) == "redeclare() takes a model's field, not 'first_name'."


def test_if_unset_holds_its_value():
    assert if_unset("Said.") == IfUnset("Said.")
