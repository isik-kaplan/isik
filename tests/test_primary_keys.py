"""docs/primary-keys.md held: every primary key in isik, and in its test projects, is a UUID called `id` from
`new_primary_key()` - but for the keys isik can't choose, and the fixtures standing in for a project's own."""

import pghistory.models
from django.apps import apps
from django.db import models

from isik.django.apps.common.db.models import new_primary_key


# Models of a project's own shape, keyed as a project might key them - what shows isik works with any key.
PROJECTS_OWN = {
    "testapp.TimestampedNote": "the timestamps alone, on Django's own integer key",
    "testapp.CleanedNote": "full_clean() on save and a repr, with neither a UUID key nor the timestamps",
    "testapp.VersionedNote": "the version alone, on Django's own integer key",
    "testapp.Team_members": "a plain many-to-many's table, which Django makes and keys",
    "testapp.Squad_members": "a plain many-to-many's table, which Django makes and keys",
    "testapp.Roster_tags": "a plain many-to-many's table, which Django makes and keys",
    "testapp.EmailUser_groups": "PermissionsMixin's many-to-many, whose table Django makes and keys",
    "testapp.EmailUser_user_permissions": "PermissionsMixin's many-to-many, whose table Django makes and keys",
}


def third_party(model):
    """A key isik can't choose: Django's own tables, pghistory's, and a history event's sequence."""
    return model.__module__.startswith(("django.", "pghistory.")) or issubclass(model, pghistory.models.Event)


def ours(model):
    """Defined by isik or by the test project - not a throwaway model a test registered on the way."""
    return model.__module__.startswith("isik.") or model.__module__ == "tests.testapp.models"


def keyed_otherwise():
    found = {}
    for model in apps.get_models(include_auto_created=True):
        concrete = model._meta.concrete_model
        if third_party(concrete) or not ours(concrete):
            continue
        pk = concrete._meta.pk
        if (pk.name, type(pk), pk.default) != ("id", models.UUIDField, new_primary_key):
            found[model._meta.label] = concrete._meta.label
    return found


def test_every_key_is_a_uuid_called_id_from_new_primary_key():
    otherwise = keyed_otherwise()

    assert {label for label, concrete in otherwise.items() if concrete not in PROJECTS_OWN} == set()


def test_each_of_the_projects_own_is_still_keyed_otherwise():
    assert set(PROJECTS_OWN) <= set(keyed_otherwise().values())
