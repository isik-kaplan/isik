"""documentation_problems() - which fields don't say what they hold."""

import types
import uuid

import pytest
from django.db import models
from django.test.utils import isolate_apps
from pghistory.models import Event

from isik.common.utils.exemptions import Exemption, exemption_class
from isik.django.apps.common.db.documentation import documentation_problems
from isik.django.apps.common.generated import mark_generated


NoHelpText = exemption_class(
    "NoDocumentationHelpText",
    rule="tests.documentation-help-text",
    why="A test's own type of exemption from saying what a field holds.",
)
NoComment = exemption_class(
    "NoDocumentationComment",
    rule="tests.documentation-comment",
    why="Another test type of exemption from saying what a field holds.",
)
REASON = "the labels of its choices already say what it holds"


@pytest.fixture(autouse=True)
def isolated():
    with isolate_apps("tests.testapp"):
        yield


def model(fields, meta=None, bases=(models.Model,)):
    attrs = {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp", **(meta or {})}), **fields}
    return type(f"Documented{uuid.uuid4().hex[:8]}", bases, attrs)


def problems(*models_, exemption_types=(Exemption,)):
    return documentation_problems([types.SimpleNamespace(get_models=lambda: models_)], exemption_types)


class TestAField:
    def test_saying_what_it_holds_in_both_is_fine(self):
        made = model({"name": models.CharField(max_length=5, help_text="Its name.", db_comment="Its name.")})

        assert problems(made) == []

    def test_without_either_is_named_once(self):
        made = model({"name": models.CharField(max_length=5)})

        assert problems(made) == [
            f"testapp.{made.__name__}.name has no help_text or db_comment - say what it holds, or give an "
            "exemption why not."
        ]

    def test_without_one_names_that_one(self):
        no_comment = model({"name": models.CharField(max_length=5, help_text="Its name.")})
        no_help = model({"name": models.CharField(max_length=5, db_comment="Its name.")})

        assert [problem.split(" has ")[1].split(" - ")[0] for problem in problems(no_comment, no_help)] == [
            "no db_comment",
            "no help_text",
        ]

    def test_blank_text_says_nothing(self):
        made = model({"name": models.CharField(max_length=5, help_text="  ", db_comment=" ")})

        assert len(problems(made)) == 1

    def test_an_exemption_answers_for_either(self):
        made = model(
            {
                "name": models.CharField(
                    max_length=5, help_text=NoHelpText(reason=REASON), db_comment=NoComment(reason=REASON)
                )
            }
        )

        assert problems(made) == []

    def test_only_an_exemption_of_the_types_given(self):
        made = model(
            {"name": models.CharField(max_length=5, help_text=NoHelpText(reason=REASON), db_comment="Its name.")}
        )

        assert len(problems(made, exemption_types=[NoComment])) == 1
        assert problems(made, exemption_types=[NoHelpText]) == []

    def test_a_many_to_many_needs_no_comment(self):
        made = model({"others": models.ManyToManyField("auth.Group", help_text="Its groups.")})

        assert problems(made) == []

    def test_an_unmanaged_models_fields_need_no_comment(self):
        made = model({"name": models.CharField(max_length=5, help_text="Its name.")}, meta={"managed": False})

        assert problems(made) == []

    def test_the_implicit_id_is_left_out(self):
        assert problems(model({})) == []


class TestModelsLeftOut:
    def test_a_proxy(self):
        base = model({"name": models.CharField(max_length=5, help_text="Its name.", db_comment="Its name.")})
        proxy = model({}, meta={"proxy": True}, bases=(base,))

        assert problems(proxy) == []

    def test_one_left_out_doesnt_end_the_list(self):
        base = model({"name": models.CharField(max_length=5, help_text="Its name.", db_comment="Its name.")})
        proxy = model({}, meta={"proxy": True}, bases=(base,))

        assert len(problems(proxy, model({"name": models.CharField(max_length=5)}))) == 1

    def test_an_auto_created_one(self):
        made = model({"name": models.CharField(max_length=5)}, meta={"auto_created": True})

        assert problems(made) == []

    def test_one_isik_generated(self):
        made = mark_generated(model({"name": models.CharField(max_length=5)}), "maker")

        assert problems(made) == []

    def test_a_pghistory_event_model(self):
        made = model({"name": models.CharField(max_length=5), "pgh_tracked_model": model({})}, bases=(Event,))

        assert problems(made) == []


def library_user(name="Member", extra=None, bases=None):
    """A user model on django.contrib.auth's AbstractUser - AbstractBaseUser and PermissionsMixin, a mixin."""
    from django.contrib.auth.models import AbstractUser

    attrs = {
        "groups": models.ManyToManyField("auth.Group", related_name="+", help_text="Its groups."),
        "user_permissions": models.ManyToManyField("auth.Permission", related_name="+", help_text="Its permissions."),
        **(extra or {}),
    }
    return model(attrs, bases=bases or (AbstractUser,))


def named(problems):
    return sorted(problem.split(" has ")[0].rsplit(".", 1)[1] for problem in problems)


class TestFieldsInheritedFromALibrary:
    def test_are_checked_by_default(self):
        flagged = named(problems(library_user()))

        assert {"username", "first_name", "password", "is_superuser", "date_joined"} <= set(flagged)

    def test_left_out_when_asked(self):
        made = library_user(extra={"nickname": models.CharField(max_length=5)})

        assert named(documentation_problems_of(made, inherited_from_libraries=False)) == ["nickname"]

    def test_one_the_project_declares_again_is_its_own(self):
        made = library_user(extra={"email": models.EmailField()})

        assert named(documentation_problems_of(made, inherited_from_libraries=False)) == ["email"]

    def test_through_a_projects_own_abstract_base_still_the_librarys(self):
        from django.contrib.auth.models import AbstractUser

        base = model({}, meta={"abstract": True}, bases=(AbstractUser,))

        assert documentation_problems_of(library_user(bases=(base,)), inherited_from_libraries=False) == []

    def test_one_from_a_projects_own_abstract_base_is_checked(self):
        base = model({"nickname": models.CharField(max_length=5)}, meta={"abstract": True})

        assert named(documentation_problems_of(model({}, bases=(base,)), inherited_from_libraries=False)) == [
            "nickname"
        ]


def documentation_problems_of(*models_, inherited_from_libraries):
    return documentation_problems(
        [types.SimpleNamespace(get_models=lambda: models_)], (Exemption,), inherited_from_libraries
    )


class TestALibrarysPath:
    def test_installed_or_isiks(self):
        from pathlib import Path

        import isik
        from isik.django.apps.common.project import is_library_path

        assert is_library_path("/venv/lib/python3.12/site-packages/oauth2_provider/models.py")
        assert is_library_path("/usr/lib/python3/dist-packages/django_tenants/models.py")
        assert is_library_path(str(Path(isik.__file__).parent / "django" / "models.py"))
        assert not is_library_path(__file__)
