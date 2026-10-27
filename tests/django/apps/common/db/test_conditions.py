"""Conditions leaving a model's database-kept fields out, and the check over the ones that don't."""

import uuid

import pgtrigger
import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.migrations.state import ProjectState
from django.test.utils import isolate_apps

from isik.django.apps.common.db import conditions
from isik.django.apps.common.db.conditions import (
    AllChange,
    AllDontChange,
    AnyChange,
    AnyDontChange,
    kept_field_condition_problems,
)
from isik.django.apps.common.db.models import DatabaseTimestampsModel
from tests.testapp.models import CleanedNote, TimestampedNote, VersionedWidget, Widget


def trigger(condition, when=pgtrigger.After):
    return pgtrigger.Trigger(
        name="made", when=when, operation=pgtrigger.Update, condition=condition, func="RETURN NULL;"
    )


class TestTheConditions:
    @pytest.mark.parametrize(
        ("ours", "theirs"),
        [
            (AnyChange, pgtrigger.AnyChange),
            (AnyDontChange, pgtrigger.AnyDontChange),
            (AllChange, pgtrigger.AllChange),
            (AllDontChange, pgtrigger.AllDontChange),
        ],
    )
    def test_each_compares_what_pgtriggers_does_less_the_kept_fields(self, ours, theirs):
        made = ours(exclude=["name"]).resolve(VersionedWidget)

        assert made == theirs(exclude=["name", "version", "updated_at"]).resolve(VersionedWidget)
        assert isinstance(ours(), theirs)

    def test_named_fields_and_exclude_auto_are_kept(self):
        assert AnyChange("name", exclude_auto=True).resolve(VersionedWidget) == (
            pgtrigger.AnyChange("name").resolve(VersionedWidget)
        )

    def test_a_negated_one_stays_negated(self):
        assert (~AnyChange()).resolve(TimestampedNote) == f"NOT ({AnyChange().resolve(TimestampedNote)})"

    def test_resolving_leaves_the_condition_as_it_was(self):
        made = AnyChange(exclude=["name"])

        made.resolve(VersionedWidget)

        assert made.exclude == ["name"]

    def test_a_model_keeping_none_is_compared_whole(self):
        assert AnyChange().resolve(CleanedNote) == "OLD.* IS DISTINCT FROM NEW.*"

    def test_a_migrations_model_is_read_by_its_registered_one(self):
        historical = ProjectState.from_apps(apps).apps.get_model("testapp", "VersionedWidget")

        assert AnyChange().resolve(historical) == AnyChange().resolve(VersionedWidget)

    @isolate_apps("tests.testapp")
    def test_a_model_no_longer_registered_keeps_none(self):
        made = type(
            f"Gone{uuid.uuid4().hex[:8]}",
            (DatabaseTimestampsModel,),
            {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
        )

        assert AnyChange().resolve(made) == "OLD.* IS DISTINCT FROM NEW.*"

    def test_naming_a_kept_field_is_refused(self):
        with pytest.raises(ValueError) as raised:
            AnyChange("name", "updated_at", "version").resolve(VersionedWidget)

        assert str(raised.value) == (
            "AnyChange names updated_at, version on testapp.VersionedWidget, which it leaves out - "
            "the database keeps them."
        )


class TestWhatAConditionReads:
    def read(self, condition, model=VersionedWidget, when=pgtrigger.After):
        return conditions._kept_fields_read(trigger(condition, when), model)

    def test_every_column_after_the_write_is_fine(self):
        assert self.read(pgtrigger.AnyChange()) == []
        assert self.read(pgtrigger.AnyDontChange()) == []

    def test_every_column_before_it_isnt(self):
        assert self.read(pgtrigger.AnyChange(), when=pgtrigger.Before) == ["version", "updated_at"]

    def test_some_columns_including_a_kept_one_arent(self):
        assert self.read(pgtrigger.AnyChange(exclude=["name"])) == ["version", "updated_at"]
        assert self.read(pgtrigger.AllChange("name", "updated_at")) == ["updated_at"]

    def test_columns_leaving_the_kept_ones_out_are_fine(self):
        assert self.read(pgtrigger.AnyChange("name")) == []
        assert self.read(pgtrigger.AnyChange(exclude=["version", "updated_at"])) == []
        assert self.read(AnyChange(exclude=["name"]), when=pgtrigger.Before) == []

    @isolate_apps("tests.testapp")
    @pytest.mark.parametrize("option", ["auto_now", "auto_now_add"])
    def test_exclude_auto_leaves_the_auto_fields_out(self, option):
        made = type(
            f"Auto{uuid.uuid4().hex[:8]}",
            (DatabaseTimestampsModel,),
            {
                "__module__": __name__,
                "Meta": type("Meta", (), {"app_label": "testapp"}),
                "seen": models.DateTimeField(**{option: True}),
            },
        )

        assert conditions._auto_fields(made) == {"seen"}
        assert self.read(pgtrigger.AnyChange(), made) == []
        assert self.read(pgtrigger.AnyChange(exclude_auto=True), made) == ["updated_at"]
        assert self.read(pgtrigger.AnyChange(exclude=["updated_at"], exclude_auto=True), made) == []

    def test_a_q_reads_its_keys_and_its_f_values(self):
        assert self.read(pgtrigger.Q(new__updated_at__gt=pgtrigger.F("old__updated_at"))) == ["updated_at"]
        assert self.read(pgtrigger.Q(new__name=pgtrigger.F("old__version"))) == ["version"]
        assert self.read(pgtrigger.Q(new__name="a")) == []
        assert self.read(pgtrigger.Q(new__name=pgtrigger.F("old__name"))) == []

    def test_every_key_of_a_q_is_read(self):
        assert self.read(pgtrigger.Q(new__updated_at__gt=1, new__version__gt=1)) == ["version", "updated_at"]

    def test_a_q_inside_a_q_is_read_beside_its_keys(self):
        either = pgtrigger.Q(new__name="b") | pgtrigger.Q(new__version__gt=1)

        assert self.read(pgtrigger.Q(new__updated_at__gt=1) & either) == ["version", "updated_at"]
        assert self.read(either & pgtrigger.Q(new__updated_at__gt=1)) == ["version", "updated_at"]

    def test_a_q_reads_a_key_by_its_column(self):
        assert self.read(pgtrigger.Q(new__owner_id=pgtrigger.F("old__owner_id")), model=Widget) == []

    def test_sql_and_no_condition_are_read_as_nothing(self):
        assert self.read(pgtrigger.Condition("NEW.updated_at > OLD.updated_at")) == []
        assert self.read(None) == []

    def test_a_model_keeping_none_has_none_to_read(self):
        assert self.read(pgtrigger.AnyChange(exclude=["count"]), model=CleanedNote) == []


class TestTheProblems:
    def test_one_sentence_per_trigger_of_the_apps_given(self, monkeypatch):
        registered = [
            (VersionedWidget, trigger(pgtrigger.AnyChange(exclude=["name"]))),
            (TimestampedNote, trigger(pgtrigger.AnyChange(), when=pgtrigger.Before)),
            (Widget, trigger(pgtrigger.AnyChange("name"))),
        ]
        monkeypatch.setattr(pgtrigger, "registered", lambda: registered)

        assert kept_field_condition_problems([apps.get_app_config("testapp")]) == [
            "testapp.VersionedWidget's trigger made has a condition comparing version, updated_at, which the "
            "database moves whenever any other column changes - leave them out of it.",
            "testapp.TimestampedNote's trigger made has a condition comparing updated_at, which the database "
            "moves whenever any other column changes - leave them out of it.",
        ]

    def test_another_apps_triggers_arent_read(self, monkeypatch):
        registered = [
            (ContentType, trigger(pgtrigger.AnyChange("name"))),
            (VersionedWidget, trigger(pgtrigger.AnyChange("version", "name"))),
        ]
        monkeypatch.setattr(pgtrigger, "registered", lambda: registered)

        assert kept_field_condition_problems([apps.get_app_config("isik_common")]) == []
        assert len(kept_field_condition_problems([apps.get_app_config("testapp")])) == 1

    def test_the_test_project_has_none(self):
        assert kept_field_condition_problems([apps.get_app_config("testapp")]) == []
