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
