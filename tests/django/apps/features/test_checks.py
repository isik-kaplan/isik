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
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "HOLDERS": None, "SWEEP": None}

        assert checks.setting_problems() == ["ISIK_FEATURES has keys it doesn't know: HOLDERS, SWEEP."]

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
