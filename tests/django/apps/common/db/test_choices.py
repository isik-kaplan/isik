"""with_choice_rules() and EnumField - retired values and transitions, held in Python and at the column."""

import types

import pgtrigger
import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.test.utils import isolate_apps

from isik.django.apps.common.db import EnumField, FullCleanOnSaveModel, with_choice_rules
from isik.django.apps.common.db.choices import (
    ChoiceRules,
    ChoiceRulesTrigger,
    choice_rule_problems,
    choice_rules_trigger,
)
from isik.django.apps.common.db.constraints import constrain
from tests.testapp.models import Manuscript, Stage


STAGE = "testapp.Manuscript:stage_choice_rules"


def errors(instance, **kwargs):
    with pytest.raises(ValidationError) as raised:
        instance.full_clean(**kwargs)
    return {
        name: [(error.code, error.message % (error.params or {})) for error in each]
        for name, each in raised.value.error_dict.items()
    }


def legacy(**kwargs):
    """A row holding the retired stage - written past the trigger, as rows from before it was retired were."""
    with pgtrigger.ignore(STAGE):
        Manuscript.objects.bulk_create([Manuscript(stage="legacy", **kwargs)])
    return Manuscript.objects.get()


class TestWithChoiceRules:
    def field(self, **kwargs):
        return models.CharField(max_length=10, choices=[("a", "A"), ("b", "B")], **kwargs)

    def test_returns_the_field_with_its_rules(self):
        field = self.field()

        assert with_choice_rules(field, retired=("old",), transitions={"a": {"b"}, "old": ["a"]}) is field
        assert field.choice_rules == ChoiceRules(
            retired=("old",), transitions={"a": frozenset({"b"}), "old": frozenset({"a"})}
        )

    def test_values_are_the_fields_own(self):
        field = with_choice_rules(
            models.IntegerField(choices=[(1, "One"), (2, "Two")]), retired=["3"], transitions={"1": ("2",)}
        )

        assert field.choice_rules == ChoiceRules(retired=(3,), transitions={1: frozenset({2})})

    def test_retired_alone(self):
        assert with_choice_rules(self.field(), retired=("old",)).choice_rules.transitions is None

    def test_transitions_alone(self):
        assert with_choice_rules(self.field(), transitions={}).choice_rules == ChoiceRules(retired=(), transitions={})

    @pytest.mark.parametrize(
        ("field", "kwargs", "message"),
        [
            (models.CharField(max_length=10), {"retired": ("a",)}, "with_choice_rules() takes a field with choices."),
            (None, {}, "with_choice_rules() takes retired=, transitions= or both."),
            (None, {"retired": "old"}, "retired= is a tuple of values, not one value."),
            (None, {"retired": (None,)}, "retired= holds values, and null is the column's own - never a retired one."),
            (
                None,
                {"retired": ("b", "a", "old")},
                "b, a are choices of the field still, and a retired value is out of them.",
            ),
            (
                None,
                {"transitions": {"a": {"z", "c"}, "y": set()}},
                "transitions= names c, y, z, which the field neither offers nor retires.",
            ),
            (
                None,
                {"transitions": {"a": {None}}},
                "transitions= names None, which the field neither offers nor retires.",
            ),
            (
                None,
                {"retired": ("old", "older"), "transitions": {"a": {"older", "old", "b"}}},
                "transitions= moves rows to old, older, which are retired.",
            ),
        ],
    )
    def test_refused(self, field, kwargs, message):
        with pytest.raises(TypeError) as raised:
            with_choice_rules(field or self.field(), **kwargs)

        assert str(raised.value) == message
        assert not hasattr(field, "choice_rules")

    def test_null_moves_on_a_nullable_field(self):
        field = with_choice_rules(self.field(null=True), transitions={None: {"a"}, "a": {None}})

        assert field.choice_rules.transitions == {None: frozenset({"a"}), "a": frozenset({None})}


class TestEnumField:
    def test_text(self):
        field = EnumField(Stage, default=Stage.DRAFT)

        assert type(field) is models.CharField
        assert (field.max_length, field.choices, field.default) == (9, Stage.choices, "draft")
        assert not hasattr(field, "choice_rules")

    def test_text_given_its_length(self):
        assert EnumField(Stage, max_length=40).max_length == 40

    def test_text_with_an_empty_choice(self):
        class Size(models.TextChoices):
            SMALL = "s", "Small"
            LARGE = "large", "Large"
            __empty__ = "Unknown"

        assert EnumField(Size, null=True).max_length == 5

    def test_integer(self):
        class Level(models.IntegerChoices):
            LOW = 1, "Low"

        field = EnumField(Level, null=True)

        assert type(field) is models.IntegerField
        assert (field.choices, field.null) == (Level.choices, True)

    @pytest.mark.parametrize("enum", [models.Choices, type("Plain", (), {}), Stage.DRAFT, ["a"]])
    def test_anything_else_refused(self, enum):
        with pytest.raises(TypeError) as raised:
            EnumField(enum)

        assert str(raised.value) == "EnumField() takes a TextChoices or IntegerChoices enum."

    def test_with_rules(self):
        field = EnumField(Stage, retired=("legacy",), transitions={Stage.DRAFT: {Stage.REVIEW}})

        assert field.choice_rules == ChoiceRules(retired=("legacy",), transitions={"draft": frozenset({"review"})})

    def test_with_retired_alone(self):
        assert EnumField(Stage, retired=("legacy",)).choice_rules.transitions is None

    def test_with_transitions_alone(self):
        assert EnumField(Stage, transitions={}).choice_rules.retired == ()

    def test_its_migration_is_the_plain_fields(self):
        field = Manuscript._meta.get_field("stage")

        assert field.deconstruct()[1:] == (
            "django.db.models.CharField",
            [],
            {"choices": Stage.choices, "default": "draft", "max_length": 9},
        )


@pytest.mark.django_db
class TestInPython:
    def test_a_new_row_takes_any_choice(self):
        Manuscript.objects.create(stage=Stage.PUBLISHED, level=2)

        assert Manuscript.objects.get().stage == "published"

    def test_a_new_row_never_takes_a_retired_value(self):
        assert errors(Manuscript(stage="legacy")) == {
            "stage": [("retired_choice", "legacy is a retired stage - only a row already holding it keeps it.")]
        }

    def test_a_row_holding_a_retired_value_keeps_it(self):
        row = legacy()
        row.title = "kept"
        row.save()

        assert Manuscript.objects.get().title == "kept"

    def test_a_row_moves_off_a_retired_value(self):
        row = legacy()
        row.stage = Stage.PUBLISHED
        row.save()

        assert Manuscript.objects.get().stage == "published"

    def test_a_row_moves_to_a_retired_value_never(self):
        row = Manuscript.objects.create()
        row.stage = "legacy"

        assert errors(row) == {
            "stage": [("retired_choice", "legacy is a retired stage - only a row already holding it keeps it.")]
        }

    def test_a_move_listed(self):
        row = Manuscript.objects.create()
        row.stage = Stage.REVIEW
        row.save()

        assert Manuscript.objects.get().stage == "review"

    def test_a_move_unlisted(self):
        row = Manuscript.objects.create(stage=Stage.REVIEW)
        row.stage = "legacy"
        assert errors(row)["stage"][0][0] == "retired_choice"

        row = Manuscript.objects.create()
        row.stage = Stage.PUBLISHED
        assert errors(row) == {
            "stage": [("invalid_transition", "stage moves from draft to review, never to published.")]
        }

    def test_a_move_from_a_value_moving_nowhere(self):
        row = Manuscript.objects.create(stage=Stage.PUBLISHED)
        row.stage = Stage.DRAFT

        assert errors(row) == {
            "stage": [("invalid_transition", "stage moves from published to nothing, never to draft.")]
        }

    def test_a_move_from_one_with_several(self):
        row = legacy()
        row.stage = Stage.DRAFT

        assert errors(row) == {
            "stage": [("invalid_transition", "stage moves from legacy to published, never to draft.")]
        }

        row = Manuscript.objects.create(stage=Stage.REVIEW, level=2)
        row.level = 3
        assert errors(row)["level"][0][0] == "invalid_choice"
        row.level = 1
        row.stage = Stage.PUBLISHED
        row.save()
        row.level = None
        assert errors(row) == {"level": [("invalid_transition", "level moves from 1 to 2, never to None.")]}

    def test_null_moves_as_a_value(self):
        row = Manuscript.objects.create()
        row.level = 2
        assert errors(row) == {"level": [("invalid_transition", "level moves from None to 1, never to 2.")]}

        row.level = 1
        row.save()
        row.level = 2
        row.save()
        row.level = None
        row.save()

        assert Manuscript.objects.get().level is None

    def test_held_against_the_last_write_not_the_commit(self):
        row = Manuscript.objects.create()
        row.stage = Stage.REVIEW
        row.save()
        row.stage = Stage.PUBLISHED
        row.save()

        assert Manuscript.objects.get().stage == "published"

    def test_a_write_leaving_the_field_out_remembers_nothing(self):
        row = Manuscript.objects.create()
        row.stage = Stage.REVIEW
        row.title = "x"
        with row.skip_full_clean():
            row.save(update_fields=["title"])
        row.stage = Stage.PUBLISHED

        assert errors(row)["stage"] == [("invalid_transition", "stage moves from draft to review, never to published.")]

    def test_a_write_naming_the_field_remembers_it(self):
        row = Manuscript.objects.create()
        row.stage = Stage.REVIEW
        row.save(update_fields=["stage"])
        row.stage = Stage.PUBLISHED

        row.full_clean()

    def test_a_write_skipping_hooks_remembers(self):
        row = Manuscript.objects.create()
        row.stage = Stage.REVIEW
        row.save(_skip_hooks=True)
        row.stage = Stage.PUBLISHED

        row.full_clean()

    def test_a_refresh_remembers(self):
        row = Manuscript.objects.create()
        Manuscript.objects.update(stage=Stage.REVIEW)
        row.refresh_from_db(fields=["title"])
        row.stage = Stage.PUBLISHED
        assert errors(row)["stage"][0][0] == "invalid_transition"

        row.refresh_from_db(fields=["stage"])
        row.stage = Stage.PUBLISHED
        row.full_clean()

        Manuscript.objects.update(stage=Stage.PUBLISHED)
        row.refresh_from_db()
        assert row._stored_choices == {"stage": "published", "level": None}

    def test_a_refresh_reads_as_asked(self, monkeypatch):
        row = Manuscript.objects.create()
        asked = []
        monkeypatch.setattr(models.Model, "refresh_from_db", lambda self, **kwargs: asked.append(kwargs))
        queryset = Manuscript.objects.all()
        row.refresh_from_db(using="default", fields=["stage"], from_queryset=queryset)

        assert asked == [{"using": "default", "fields": ["stage"], "from_queryset": queryset}]

    def test_a_deferred_field_remembered_when_read(self):
        Manuscript.objects.create(stage=Stage.REVIEW)
        row = Manuscript.objects.only("title").get()
        assert row._stored_choices == {}

        row.stage = Stage.DRAFT
        row.full_clean()
        assert "stage" not in row._stored_choices

        row = Manuscript.objects.only("title").get()
        assert row.stage == "review"
        assert row._stored_choices == {"stage": "review"}

    def test_a_row_never_read_is_left_to_the_column(self):
        row = Manuscript(id=Manuscript.objects.create(stage=Stage.PUBLISHED).id, stage="legacy")
        row._state.adding = False
        row.full_clean(validate_unique=False)

        row.stage = Stage.DRAFT
        row.full_clean(validate_unique=False)

    def test_an_excluded_field_is_not_held(self):
        row = Manuscript.objects.create(stage=Stage.PUBLISHED)
        row.stage = Stage.DRAFT

        row.full_clean(exclude=["stage"])

    def test_other_fields_still_cleaned_beside(self):
        row = Manuscript.objects.create(stage=Stage.PUBLISHED)
        row.stage = Stage.DRAFT
        row.title = "x" * 21

        assert set(errors(row)) == {"stage", "title"}

    def test_a_value_out_of_the_choices(self):
        assert errors(Manuscript(stage="gone")) == {
            "stage": [("invalid_choice", "Value 'gone' is not a valid choice.")]
        }

    def test_a_value_the_field_cant_read(self):
        assert errors(Manuscript(level="high"))["level"][0][0] == "invalid"

    def test_cleaned_to_the_fields_value(self):
        row = Manuscript(level="1")
        row.full_clean()

        assert row.level == 1

    def test_a_blank_value_taken_uncleaned(self):
        field = with_choice_rules(models.CharField(max_length=3, choices=[("a", "A")], blank=True), retired=("b",))
        field.set_attributes_from_name("size")
        holder = types.SimpleNamespace(_state=types.SimpleNamespace(adding=True))

        assert field.choice_rules.clean(field, "", holder) == ""
        assert field.choice_rules.clean(field, None, holder) is None

    def test_the_instance_reaches_the_fields_own_validation(self):
        seen = []

        class Seeing(models.CharField):
            def validate(self, value, model_instance):
                seen.append(model_instance)
                super().validate(value, model_instance)

        field = with_choice_rules(Seeing(max_length=3, choices=[("a", "A")]), retired=("b",))
        field.set_attributes_from_name("size")
        holder = types.SimpleNamespace(_state=types.SimpleNamespace(adding=True))

        assert field.choice_rules.clean(field, "a", holder) == "a"
        assert seen == [holder]

    def test_the_moves_named_in_order(self):
        field = with_choice_rules(
            models.IntegerField(choices=[(n, str(n)) for n in (1, 2, 9, 10)], null=True),
            transitions={1: {10, None, 9, 2}},
        )
        field.set_attributes_from_name("size")
        holder = types.SimpleNamespace(_state=types.SimpleNamespace(adding=False), _stored_choices={"size": 1})

        with pytest.raises(ValidationError) as raised:
            field.choice_rules.clean(field, 1, holder)
            holder._stored_choices["size"] = 2
            field.choice_rules.clean(field, 1, holder)

        assert raised.value.messages == ["size moves from 2 to nothing, never to 1."]

        holder._stored_choices["size"] = 1
        with_choice_rules(field, transitions={1: {10, None, 9}})
        with pytest.raises(ValidationError) as raised:
            field.choice_rules.clean(field, 2, holder)

        assert raised.value.messages == ["size moves from 1 to 10, 9, None, never to 2."]

    def test_a_retired_value_held_to_the_fields_validators(self):
        field = with_choice_rules(models.CharField(max_length=3, choices=[("a", "A")]), retired=("long",))
        field.set_attributes_from_name("size")
        holder = types.SimpleNamespace(_state=types.SimpleNamespace(adding=False), _stored_choices={"size": "long"})

        with pytest.raises(ValidationError) as raised:
            field.choice_rules.clean(field, "long", holder)

        assert [error.code for error in raised.value.error_list] == ["max_length"]

    def test_a_retired_value_cleaned_to_the_fields_value(self):
        field = with_choice_rules(models.IntegerField(choices=[(1, "One")]), retired=(5,))
        field.set_attributes_from_name("size")
        holder = types.SimpleNamespace(_state=types.SimpleNamespace(adding=False), __dict__={})
        holder.__dict__["_stored_choices"] = {"size": 5}

        assert field.choice_rules.clean(field, "5", holder) == 5


@pytest.mark.django_db
class TestAtTheColumn:
    def refused(self, write):
        with pytest.raises(IntegrityError) as raised, transaction.atomic():
            write()
        return str(raised.value).splitlines()[0]

    def test_a_retired_value_inserted(self):
        assert self.refused(lambda: Manuscript.objects.bulk_create([Manuscript(stage="legacy")])) == (
            "'legacy' is a retired stage - only a row already holding it keeps it."
        )

    def test_a_retired_value_written(self):
        Manuscript.objects.create()

        assert self.refused(lambda: Manuscript.objects.update(stage="legacy")) == (
            "'legacy' is a retired stage - only a row already holding it keeps it."
        )

    def test_a_row_holding_one_keeps_it(self):
        legacy()
        Manuscript.objects.update(stage="legacy", title="kept")

        assert Manuscript.objects.get().title == "kept"

    def test_a_move_unlisted(self):
        Manuscript.objects.create()

        assert self.refused(lambda: Manuscript.objects.update(stage="published")) == (
            "stage never moves from 'draft' to 'published'."
        )

    def test_a_move_listed(self):
        legacy()
        Manuscript.objects.update(stage="published")

        assert Manuscript.objects.get().stage == "published"

    def test_null_moves(self):
        Manuscript.objects.create()
        assert self.refused(lambda: Manuscript.objects.update(level=2)) == "level never moves from NULL to '2'."

        Manuscript.objects.update(level=1)
        assert self.refused(lambda: Manuscript.objects.update(level=None)) == "level never moves from '1' to NULL."

        Manuscript.objects.update(level=2)
        Manuscript.objects.update(level=None)
        assert Manuscript.objects.get().level is None

    def test_an_insert_takes_any_choice(self):
        Manuscript.objects.bulk_create([Manuscript(stage="published", level=2)])

        assert Manuscript.objects.get().level == 2

    def test_the_condition_admits_the_retired_values(self):
        from isik.django.apps.common.db.constraints import _choices_condition

        field = with_choice_rules(models.CharField(max_length=9, choices=Stage.choices), retired=("old", "older"))
        field.set_attributes_from_name("stage")

        assert _choices_condition(field) == models.Q(stage__in=["draft", "review", "published", "old", "older"])

    def test_the_check_admits_the_retired_value(self):
        constraint = next(c for c in Manuscript._meta.constraints if c.name == "testapp_manuscript_stage_choices")

        assert constraint.condition == models.Q(stage__in=["draft", "review", "published", "legacy"])
        assert constraint.violation_error_message == "stage must be one of: draft, review, published."


class TestTheTrigger:
    def func(self, field):
        return choice_rules_trigger(field).get_func(Manuscript)

    def test_named_for_its_field(self):
        trigger = choice_rules_trigger(Manuscript._meta.get_field("stage"))

        assert (trigger.name, trigger.field, trigger.rules) == (
            "stage_choice_rules",
            "stage",
            Manuscript._meta.get_field("stage").choice_rules,
        )
        assert (trigger.when, str(trigger.operation)) == (pgtrigger.Before, "INSERT OR UPDATE")

    def test_moving_nowhere(self):
        field = with_choice_rules(models.CharField(max_length=9, choices=Stage.choices), transitions={})
        field.set_attributes_from_name("stage")

        assert self.func(field) == (
            "IF TG_OP = 'INSERT' OR NEW.\"stage\" IS DISTINCT FROM OLD.\"stage\" THEN IF TG_OP = 'UPDATE' AND NOT "
            "(FALSE) THEN RAISE EXCEPTION USING ERRCODE = 'check_violation', MESSAGE = format('stage never moves from "
            '%L to %L.\', OLD."stage", NEW."stage"); END IF; END IF; RETURN NEW;'
        )

    def test_retired_alone(self):
        field = with_choice_rules(models.CharField(max_length=9, choices=Stage.choices), retired=("it's", "b"))
        field.set_attributes_from_name("stage")

        assert self.func(field) == (
            "IF TG_OP = 'INSERT' OR NEW.\"stage\" IS DISTINCT FROM OLD.\"stage\" THEN IF NEW.\"stage\" IN ('it''s', "
            "'b') THEN RAISE EXCEPTION USING ERRCODE = 'check_violation', MESSAGE = format('%L is a retired stage - "
            'only a row already holding it keeps it.\', NEW."stage"); END IF; END IF; RETURN NEW;'
        )

    def test_its_column(self):
        field = with_choice_rules(
            models.CharField(max_length=9, choices=Stage.choices, db_column="phase"), transitions={"draft": {"review"}}
        )
        field.set_attributes_from_name("stage")
        model = types.SimpleNamespace(_meta=types.SimpleNamespace(get_field={"stage": field}.get))

        assert choice_rules_trigger(field).get_func(model) == (
            "IF TG_OP = 'INSERT' OR NEW.\"phase\" IS DISTINCT FROM OLD.\"phase\" THEN IF TG_OP = 'UPDATE' AND NOT "
            "((OLD.\"phase\" IS NOT DISTINCT FROM 'draft' AND NEW.\"phase\" IS NOT DISTINCT FROM 'review')) THEN "
            "RAISE EXCEPTION USING ERRCODE = 'check_violation', MESSAGE = format('stage never moves from %L to %L.', "
            'OLD."phase", NEW."phase"); END IF; END IF; RETURN NEW;'
        )

    def test_retired_and_moves_with_null(self):
        field = with_choice_rules(
            models.CharField(max_length=9, choices=Stage.choices, null=True),
            retired=("old",),
            transitions={None: {"draft"}, "old": {"review", None}},
        )
        field.set_attributes_from_name("stage")

        assert self.func(field) == (
            'IF TG_OP = \'INSERT\' OR NEW."stage" IS DISTINCT FROM OLD."stage" THEN IF NEW."stage" IN (\'old\') THEN '
            "RAISE EXCEPTION USING ERRCODE = 'check_violation', MESSAGE = format('%L is a retired stage - only a row "
            'already holding it keeps it.\', NEW."stage"); END IF; IF TG_OP = \'UPDATE\' AND NOT ((OLD."stage" IS '
            "NOT DISTINCT FROM 'old' AND NEW.\"stage\" IS NOT DISTINCT FROM 'review') OR (OLD.\"stage\" IS NOT "
            'DISTINCT FROM \'old\' AND NEW."stage" IS NOT DISTINCT FROM NULL) OR (OLD."stage" IS NOT DISTINCT FROM '
            "NULL AND NEW.\"stage\" IS NOT DISTINCT FROM 'draft')) THEN RAISE EXCEPTION USING ERRCODE = "
            "'check_violation', MESSAGE = format('stage never moves from %L to %L.', OLD.\"stage\", NEW.\"stage\"); "
            "END IF; END IF; RETURN NEW;"
        )

    def test_registered_once(self):
        registered = [trigger.name for trigger in Manuscript._meta.triggers]
        constrain(Manuscript)

        assert [trigger.name for trigger in Manuscript._meta.triggers] == registered
        assert {"stage_choice_rules", "level_choice_rules"} <= set(registered)
        assert isinstance(
            next(t for t in Manuscript._meta.triggers if t.name == "level_choice_rules"), ChoiceRulesTrigger
        )

    @isolate_apps("tests.testapp")
    def test_none_on_an_unmanaged_model(self):
        class Elsewhere(FullCleanOnSaveModel):
            stage = EnumField(Stage, transitions={})

            class Meta:
                app_label = "testapp"
                managed = False

        constrain(Elsewhere)

        assert getattr(Elsewhere._meta, "triggers", []) == []
        assert [c.name for c in Elsewhere._meta.constraints] == ["testapp_elsewhere_stage_choices"]


class TestTheCheck:
    def problems(self, registry=apps):
        return [str(problem) for problem in choice_rule_problems([registry.get_app_config("testapp")])]

    def test_none_for_the_project(self):
        assert self.problems() == []

    @isolate_apps("tests.testapp")
    def test_a_model_that_cant_hold_them(self):
        class Plain(models.Model):
            stage = EnumField(Stage, transitions={})
            other = EnumField(Stage)

            class Meta:
                app_label = "testapp"

        class Unmanaged(FullCleanOnSaveModel):
            stage = EnumField(Stage, retired=("legacy",))

            class Meta:
                app_label = "testapp"
                managed = False

        assert self.problems(Plain._meta.apps) == [
            "testapp.Plain.stage has choice rules, which only a managed FullCleanOnSaveModel holds.",
            "testapp.Unmanaged.stage has choice rules, which only a managed FullCleanOnSaveModel holds.",
        ]

    def test_wired_as_e026(self, monkeypatch):
        from isik.django.apps.common import checks

        options = checks.check_options(checks.Check.CHOICE_RULES)
        given = []
        monkeypatch.setattr(
            "isik.django.apps.common.db.choices.choice_rule_problems",
            lambda configs: given.append(list(configs)) or ["one", "two"],
        )
        hint = "Base the model on FullCleanOnSaveModel, or see isik.django.apps.common.db.choices."

        assert [(m.id, m.msg, m.hint) for m in checks.every_choice_rule_is_held(options)] == [
            ("isik.E026", "one", hint),
            ("isik.E026", "two", hint),
        ]
        assert given == [list(checks._project_configs(options))]
