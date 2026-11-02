"""The features app's checks, its audience and its configuration - each problem it reports, and each way to set it."""

import uuid
from types import SimpleNamespace

import pytest
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.features import checks, conf, declarations, swapping
from isik.django.apps.features.audiences import HolderPeople
from isik.django.apps.features.bases import AbstractOverrides
from isik.django.apps.features.declarations import Default, OnHolder, OnOverrides, Setting
from isik.django.apps.features.models import Feature, FeatureGrant
from tests.testapp.models import EmailUser, Tag, UserOverrides


def a_setting(name, **kwargs):
    return Setting(
        **{
            "name": name,
            "field": models.IntegerField(),
            "default": 0,
            "feature": None,
            "without_feature": Default(),
            "own": OnOverrides(),
            "label": "A label",
            "description": None,
            "visible_to_holder": True,
            "request_policies": (),
            **kwargs,
        }
    )


class TestTheSettingsCheck:
    def test_the_projects_configuration_has_nothing_to_say(self):
        assert checks.setting_problems() == []
        assert checks.features_can_work(None) == []

    def test_a_key_it_doesnt_know(self, settings):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "HOLDERS": None, "SWEEPS": None}

        assert checks.setting_problems() == ["ISIK_FEATURES has keys it doesn't know: HOLDERS, SWEEPS."]

    @pytest.mark.parametrize("sweep", ["interval", "next_boundary"])
    def test_either_sweep(self, settings, sweep):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "SWEEP": sweep}

        assert checks.setting_problems() == []

    def test_a_sweep_it_doesnt_know(self, settings):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "SWEEP": "hourly"}

        assert checks.setting_problems() == ["ISIK_FEATURES' SWEEP is 'hourly' - it's one of interval, next_boundary."]

    def test_next_boundary_needs_celery(self, settings, monkeypatch):
        import importlib.util

        real = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "celery" else real(name))
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "SWEEP": "next_boundary"}

        assert checks.setting_problems() == [
            "ISIK_FEATURES' SWEEP is next_boundary, which schedules each run with Celery - install it."
        ]
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "SWEEP": "interval"}
        assert checks.setting_problems() == []

    def test_settings_with_nowhere_to_keep_them(self, settings):
        settings.ISIK_FEATURES_OVERRIDES_MODEL = None

        assert checks.setting_problems() == [
            "Settings are declared, and ISIK_FEATURES_OVERRIDES_MODEL names no model to keep them in - declare one "
            "on AbstractOverrides."
        ]

    def test_no_settings_need_no_model(self, settings, monkeypatch):
        settings.ISIK_FEATURES_OVERRIDES_MODEL = None
        monkeypatch.setattr(declarations, "settings", {})

        assert checks.setting_problems() == []

    def test_an_overrides_model_that_isnt_one(self, settings):
        # Not one, so there's nothing more to ask of it.
        settings.ISIK_FEATURES_OVERRIDES_MODEL = "testapp.Tag"

        assert checks.setting_problems() == ["testapp.Tag isn't an AbstractOverrides."]

    @isolate_apps("tests.testapp")
    def test_an_overrides_model_with_no_history(self, monkeypatch):
        class Untracked(AbstractOverrides):
            class Meta:
                app_label = "testapp"

        monkeypatch.setattr(checks, "get_overrides_model", lambda: Untracked)

        assert checks.setting_problems() == [
            "testapp.Untracked keeps forced settings and has no history - who forced one, when and why is gone once "
            "it's released. Track it with @track_events()."
        ]

    def test_grants_of_another_holder(self, settings):
        settings.ISIK_FEATURES_HOLDER_MODEL = "testapp.Tag"

        assert checks.setting_problems()[0] == (
            "isik_features.FeatureGrant's grants are of testapp.EmailUser, and ISIK_FEATURES_HOLDER_MODEL is "
            "testapp.Tag - declare a grant model of your own on AbstractFeatureGrant and name it in "
            "ISIK_FEATURES_GRANT_MODEL."
        )

    def test_a_holder_key_that_cascades(self, monkeypatch):
        holder = UserOverrides._meta.get_field("holder")
        monkeypatch.setattr(holder.remote_field, "on_delete", models.CASCADE)

        assert checks.setting_problems() == [
            "testapp.UserOverrides's holder key cascades - its rows refuse a delete, and their history would "
            "outlive them; on_delete=ERASE erases both."
        ]

    def test_it_is_reported_as_an_error(self, settings):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "HOLDERS": None}

        (error,) = checks.features_can_work(None)

        assert (error.id, error.hint) == ("isik.E017", "See isik.django.apps.features.")


class TestTheDeclarationsCheck:
    def test_the_projects_declarations_have_nothing_to_say(self):
        assert checks.declaration_problems() == []

    def test_an_own_value_on_a_field_the_holder_hasnt(self, monkeypatch):
        monkeypatch.setitem(declarations.settings, "nickname", a_setting("nickname", own=OnHolder("nickname")))

        assert checks.declaration_problems() == [
            "nickname's own value is the holder's nickname, which testapp.EmailUser has no field called."
        ]

    @pytest.mark.parametrize(
        "declared",
        [{"field": models.IntegerField(null=True)}, {"default": None}],
        ids=["a nullable field", "no default"],
    )
    def test_a_setting_that_could_be_null(self, monkeypatch, declared):
        monkeypatch.setitem(declarations.settings, "maybe", a_setting("maybe", **declared))

        assert checks.declaration_problems() == [
            "maybe could be null - a setting always has a value, so its field takes no null=True and its default "
            "isn't None."
        ]

    def test_a_feature_nothing_reads(self, monkeypatch):
        monkeypatch.setitem(declarations.features, "unread", declarations.FeatureName("unread"))

        assert checks.declaration_problems() == [
            "unread unlocks no setting and nothing checks it - give a setting feature=, or check it with "
            "HasFeature or requires_feature()."
        ]

    def test_a_feature_checked_is_read(self, monkeypatch):
        monkeypatch.setitem(declarations.features, "gated", declarations.FeatureName("gated"))
        monkeypatch.setattr(declarations, "checked", {*declarations.checked, "gated"})

        assert checks.declaration_problems() == []

    def test_text_a_client_could_only_show_by_name(self, monkeypatch):
        monkeypatch.setitem(declarations.features, "bare", declarations.FeatureName("bare"))
        monkeypatch.setitem(declarations.settings, "bare_setting", a_setting("bare_setting", label=None))

        assert checks.text_problems() == [
            "The feature bare has no label - give it label=.",
            "The setting bare_setting has no label - give it label=.",
        ]


@pytest.mark.django_db
class TestHolderPeople:
    def notice(self, holder_pk, field="holder"):
        return SimpleNamespace(snapshot={field: holder_pk}, subject_partition="p")

    def test_what_it_reads(self):
        people = HolderPeople()

        assert (str(people), people.snapshot_fields(), people.spans_partitions) == (
            "HolderPeople('holder')",
            ("holder",),
            False,
        )
        assert people.partitions(self.notice(1)) == ["p"]

    def test_a_user_holder_is_its_own_people(self):
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")

        assert HolderPeople().recipients(self.notice(alice.pk)) == [alice]
        assert HolderPeople("owner").recipients(self.notice(alice.pk, "owner")) == [alice]

    def test_a_holder_gone_reaches_nobody(self):
        assert HolderPeople().recipients(self.notice(str(uuid.uuid4()))) == []

    def test_the_project_says_who_a_holders_people_are(self, settings):
        alice = EmailUser.objects.create(username="alice", email="alice@example.com")
        bob = EmailUser.objects.create(username="bob", email="bob@example.com")
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "PEOPLE": lambda holder: iter([holder, bob])}

        assert HolderPeople().recipients(self.notice(alice.pk)) == [alice, bob]

    def test_a_holder_that_isnt_a_user_reaches_nobody_unless_the_project_says(self, settings):
        tag = Tag.objects.create(label="team")
        settings.ISIK_FEATURES_HOLDER_MODEL = "testapp.Tag"

        assert HolderPeople().recipients(self.notice(tag.pk)) == []


class TestConfiguration:
    def test_a_hook_by_its_dotted_path(self, settings):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "HOLDER": "tests.testapp.policies.RecentlyProved"}

        from tests.testapp.policies import RecentlyProved

        assert conf.setting("HOLDER") is RecentlyProved
        assert conf.setting("GRANT_SOURCES") == ("manual", "trial", "subscription")

    def test_the_defaults_leave_what_a_project_set(self, settings):
        swapping.set_defaults()

        assert (settings.ISIK_FEATURES_HOLDER_MODEL, settings.ISIK_FEATURES_OVERRIDES_MODEL) == (
            settings.AUTH_USER_MODEL,
            "testapp.UserOverrides",
        )
        assert swapping.get_holder_model() is EmailUser


@pytest.mark.django_db
def test_each_model_says_what_it_is():
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")

    assert str(Feature(name="beta")) == "beta"
    feature = uuid.uuid4()

    assert str(FeatureGrant(feature_id=feature, holder_id=alice.pk)) == f"{feature} for {alice.pk}"
    assert str(UserOverrides(holder_id=alice.pk)) == f"settings of {alice.pk}"


class TestTheOverridesColumns:
    @isolate_apps("tests.testapp")
    def test_built_without_a_settings_module(self, settings, monkeypatch):
        # Settings declared some other way are still columns; nothing is imported for them.
        settings.ISIK_FEATURES = {}
        monkeypatch.setattr(declarations, "settings", {"limit": a_setting("limit")})

        class Isolated(AbstractOverrides):
            class Meta:
                app_label = "testapp"

        assert {field.name for field in Isolated._meta.get_fields()} >= {"limit", "limit_forced", "limit_forced_value"}

    @isolate_apps("tests.testapp")
    def test_a_setting_that_can_be_null_may_be_forced_to_null(self, monkeypatch):
        nullable = a_setting("note", field=models.TextField(null=True), own=OnHolder("first_name"))
        monkeypatch.setattr(declarations, "settings", {"note": nullable})

        class Isolated(AbstractOverrides):
            class Meta:
                app_label = "testapp"

        (constraint,) = Isolated._meta.constraints
        assert "note" not in {field.name for field in Isolated._meta.get_fields()}
        assert "note_forced_value__isnull" not in str(constraint.condition.children[1])


class TestTheGrantHistoryCheck:
    """A grant's history is the only record of when it was revoked - so it has to be there, whole, and kept."""

    @staticmethod
    def grants(name, *track):
        from isik.django.apps.features.bases import AbstractFeatureGrant

        # A name of its own each run: pghistory refuses a second event model of one name in an app.
        model = type(
            f"{name}{uuid.uuid4().hex[:8]}",
            (AbstractFeatureGrant,),
            {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
        )
        return track[0](model) if track else model

    def test_isiks_own_has_nothing_to_say(self):
        assert checks.grant_history_problems(FeatureGrant) == []

    @isolate_apps("tests.testapp")
    def test_one_with_no_history(self):
        model = self.grants("Untracked")

        assert checks.grant_history_problems(model) == [
            f"{model._meta.label} has no history - it's what says when each grant was revoked, and the sweep reads it. "
            "Track it with @track_events()."
        ]

    @isolate_apps("tests.testapp")
    def test_one_whose_history_leaves_status_out(self):
        from isik.django.apps.common.db import track_events

        model = self.grants("NoStatus", track_events(exclude=["status"]))

        assert checks.grant_history_problems(model) == [
            f"{model._meta.label}'s history leaves out status - track it.",
            f"{model._meta.label}'s history records no update changing status - a revoke would leave no time.",
        ]

    @isolate_apps("tests.testapp")
    def test_one_whose_history_can_be_edited(self):
        from isik.django.apps.common.db import track_events

        model = self.grants("Editable", track_events(append_only=False))

        assert checks.grant_history_problems(model) == [
            f"{model._meta.label}'s history can be edited - when a grant was revoked has to stay as written. Keep it "
            "append-only."
        ]

    @pytest.mark.parametrize(
        "condition",
        [
            "excluded",
            "other_fields",
            "negated",
            "not_a_change",
        ],
    )
    @isolate_apps("tests.testapp")
    def test_one_whose_updates_dont_record_a_status_change(self, condition):
        import pghistory
        import pgtrigger

        from isik.django.apps.common.db import track_events

        conditions = {
            "excluded": pgtrigger.AnyChange(exclude=["status"]),
            "other_fields": pgtrigger.AnyChange("source"),
            "negated": ~pgtrigger.AnyChange("status"),
            "not_a_change": pgtrigger.Q(old__source="manual"),
        }
        trackers = [pghistory.InsertEvent(), pghistory.UpdateEvent(condition=conditions[condition])]

        model = self.grants("Narrow", track_events(trackers=trackers))

        assert checks.grant_history_problems(model) == [
            f"{model._meta.label}'s history records no update changing status - a revoke would leave no time."
        ]

    @pytest.mark.parametrize("condition", ["none", "named", "all"])
    @isolate_apps("tests.testapp")
    def test_updates_that_record_it(self, condition):
        import pghistory
        import pgtrigger

        from isik.django.apps.common.db import track_events

        conditions = {"none": None, "named": pgtrigger.AnyChange("status"), "all": pgtrigger.AnyChange()}
        update = (
            pghistory.UpdateEvent()
            if conditions[condition] is None
            else pghistory.UpdateEvent(condition=conditions[condition])
        )

        assert (
            checks.grant_history_problems(self.grants(f"Records{condition.title()}", track_events(trackers=[update])))
            == []
        )

    def test_an_update_event_with_no_condition_records_every_change(self):
        import copy

        import pghistory.trigger
        import pgtrigger

        events = {
            trigger.name: trigger
            for model, trigger in pgtrigger.registered()
            if model is FeatureGrant and isinstance(trigger, pghistory.trigger.Event)
        }
        unconditional = copy.copy(events["update_update"])
        unconditional.condition = None

        assert checks._records_status(unconditional)
        assert not checks._records_status(events["insert_insert"])
        assert not checks._records_status(pgtrigger.Protect(name="protect", operation=pgtrigger.Update))

    def test_its_part_of_the_settings_check(self, monkeypatch):
        monkeypatch.setattr(checks, "grant_history_problems", lambda grants: [f"about {grants._meta.label}"])

        assert checks.setting_problems() == ["about isik_features.FeatureGrant"]
