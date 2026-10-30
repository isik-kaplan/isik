"""Declaring features, settings and force sources, configuring the app, and the small acts around them."""

from datetime import timedelta

import pytest
from django.apps import apps
from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.db import models
from django.db.models.signals import post_migrate, pre_delete

from isik.django.apps.common import checks as common_checks
from isik.django.apps.common import forcing
from isik.django.apps.common.forcing import ForceSource, declare_force_source
from isik.django.apps.features import checks, conf, declarations, guards, swapping
from isik.django.apps.features.declarations import Default, OnOverrides, declare_feature, declare_setting
from isik.django.apps.features.models import Feature
from isik.django.apps.features.operations import SettingInvalid, erase_with_holder, grant, set_setting
from isik.django.apps.features.sync import mirror_features
from isik.django.drf.error_handling import refusal
from tests.testapp.models import EmailUser
from tests.testapp.policies import RecentlyProved


@pytest.fixture
def declaring(monkeypatch):
    """Declarations made in a test, gone after it."""
    monkeypatch.setattr(declarations, "features", dict(declarations.features))
    monkeypatch.setattr(declarations, "settings", dict(declarations.settings))
    monkeypatch.setattr(declarations, "checked", set(declarations.checked))
    monkeypatch.setattr(forcing, "_declared", dict(forcing._declared))


class TestDeclaringAFeature:
    def test_what_it_carries(self, declaring):
        made = declare_feature("audit", label="Audit", description="Who did what.", visible_to_holder=False)

        assert declarations.features["audit"] is made
        assert (made, made.label, made.description, made.visible_to_holder) == (
            "audit",
            "Audit",
            "Who did what.",
            False,
        )

    def test_shown_to_its_holder_unless_said(self, declaring):
        made = declare_feature("plain")

        assert (made.label, made.description, made.visible_to_holder) == (None, None, True)


class TestDeclaringASetting:
    def test_what_it_carries(self, declaring):
        audit = declare_feature("audit", label="Audit")
        field = models.IntegerField()

        made = declare_setting(
            "retention",
            field,
            30,
            feature=audit,
            without_feature=declarations.AtMost(7),
            own=declarations.OnHolder("first_name"),
            label="Retention",
            description="How long it's kept.",
            visible_to_holder=False,
            holder_may_change=False,
            request_policies=[RecentlyProved],
        )

        assert declarations.settings["retention"] is made
        assert made == declarations.Setting(
            name="retention",
            field=field,
            default=30,
            feature="audit",
            without_feature=declarations.AtMost(7),
            own=declarations.OnHolder("first_name"),
            label="Retention",
            description="How long it's kept.",
            visible_to_holder=False,
            holder_may_change=False,
            request_policies=(RecentlyProved,),
        )
        assert type(made.feature) is str

    def test_its_defaults(self, declaring):
        made = declare_setting("plain", models.IntegerField(), 1)

        assert (made.feature, made.without_feature, made.own, made.label, made.description) == (
            None,
            Default(),
            OnOverrides(),
            None,
            None,
        )
        assert (made.visible_to_holder, made.holder_may_change, made.request_policies) == (True, True, ())

    def test_a_feature_it_needs_is_declared(self, declaring):
        with pytest.raises(ImproperlyConfigured, match="^plain needs the feature nowhere, which isn't declared.$"):
            declare_setting("plain", models.IntegerField(), 1, feature="nowhere")


class TestDeclaringAForceSource:
    def test_what_it_carries(self, declaring):
        made = declare_force_source(
            "billing", label="Billing", operator_may_release=False, reason_floor=3, shows_reason_to_holder=True
        )

        assert forcing.get_force_source("billing") is made
        assert (made, made.name, type(made.name)) == ("billing", "billing", str)
        assert (made.label, made.operator_may_release, made.reason_floor, made.shows_reason_to_holder) == (
            "Billing",
            False,
            3,
            True,
        )

    def test_its_defaults(self, declaring):
        made = declare_force_source("plain")

        assert (made.label, made.operator_may_release, made.reason_floor, made.shows_reason_to_holder) == (
            None,
            True,
            40,
            False,
        )

    def test_a_forced_rows_source_compares_equal_to_its_declaration(self, declaring):
        assert declare_force_source("billing") == "billing"
        assert isinstance(declare_force_source("support"), ForceSource)


class TestConfiguring:
    def test_nothing_configured(self, settings):
        del settings.ISIK_FEATURES

        assert conf.configured() == {}
        assert conf.setting("GRANT_SOURCES") == ("manual",)

    def test_the_swappable_settings_a_project_leaves_unset(self, settings):
        del settings.ISIK_FEATURES_HOLDER_MODEL
        del settings.ISIK_FEATURES_GRANT_MODEL
        settings.ISIK_FEATURES_SWEEP_MODEL = "testapp.Tag"

        swapping.set_defaults()

        assert settings.ISIK_FEATURES_HOLDER_MODEL == settings.AUTH_USER_MODEL
        assert settings.ISIK_FEATURES_GRANT_MODEL == "isik_features.FeatureGrant"
        assert settings.ISIK_FEATURES_SWEEP_MODEL == "testapp.Tag"
        assert swapping.model_name(swapping.HOLDER_MODEL) == settings.AUTH_USER_MODEL

    def test_no_overrides_model(self, settings):
        del settings.ISIK_FEATURES_OVERRIDES_MODEL

        assert swapping.get_overrides_model() is None

    def test_the_module_declaring_the_settings_is_imported(self, monkeypatch):
        imported = []
        monkeypatch.setattr(conf, "import_module", imported.append)

        conf.import_settings_module()

        assert imported == ["tests.testapp.feature_settings"]

    def test_the_app_mirrors_after_its_own_migrate_and_erases_with_the_holder_once(self, monkeypatch):
        connected = []
        for signal in (post_migrate, pre_delete):
            monkeypatch.setattr(signal, "connect", lambda *args, **kwargs: connected.append((args, kwargs)))
        config = apps.get_app_config("isik_features")

        config.ready()

        assert connected == [
            ((mirror_features,), {"sender": config, "dispatch_uid": "isik_features_mirror"}),
            ((erase_with_holder,), {"sender": EmailUser, "dispatch_uid": "isik_features_erase"}),
        ]


@pytest.mark.django_db
class TestMirroring:
    def test_a_new_declaration_gets_its_row(self, declaring):
        declare_feature("fresh", label="Fresh")

        mirror_features()

        assert set(Feature.objects.values_list("name", flat=True)) == set(declarations.features)


class TestChecks:
    def test_a_check_marks_the_feature_read(self, declaring):
        assert guards.checks_feature(declarations.FeatureName("fresh")) == "fresh"
        assert "fresh" in declarations.checked

    def test_the_errors_say_the_problem(self, settings):
        settings.ISIK_FEATURES = {**settings.ISIK_FEATURES, "HOLDERS": None}

        assert checks.features_can_work(None) == [
            Error(
                "ISIK_FEATURES has keys it doesn't know: HOLDERS.",
                hint="See isik.django.apps.features.",
                id="isik.E017",
            )
        ]

    def test_the_labels_check_reads_each_installed_app(self, declaring):
        declare_feature("bare")

        problems = [error.msg for error in common_checks.every_declaration_says_what_it_is(None)]

        assert problems == ["The feature bare has no label - give it label=."]


@pytest.mark.django_db
class TestWriting:
    def test_a_grant_with_no_reference(self, alice):
        assert grant(alice, "beta", source="manual").reference == ""

    def test_every_reason_a_value_isnt_taken(self, alice, monkeypatch):
        def odd(value):
            raise ValidationError("Not odd.")

        def small(value):
            raise ValidationError("Not small.")

        twice = declarations.Setting("theme", models.IntegerField(validators=[odd, small]), 1)
        monkeypatch.setitem(declarations.settings, "theme", twice)

        with pytest.raises(SettingInvalid, match="^Not odd.; Not small.$"):
            set_setting(alice, "theme", 4)

    def test_a_value_on_the_holder_writes_only_its_own_field(self, alice):
        # Changed meanwhile, and not on this instance: the write mustn't put it back.
        EmailUser.objects.filter(pk=alice.pk).update(last_name="Meanwhile")

        set_setting(alice, "display_name", "Al")

        assert EmailUser.objects.values_list("first_name", "last_name").get(pk=alice.pk) == ("Al", "Meanwhile")


def test_the_sweep_commands_help():
    from isik.django.apps.features.management.commands.features import Command

    parser = Command().create_parser("manage.py", "features")

    (sweep,) = [action for action in parser._actions if "--sweep" in action.option_strings]
    assert (sweep.help, sweep.default) == ("Tell what grants starting or ending since the last sweep moved.", False)


def test_a_refusal_carries_its_message_and_code():
    refused = refusal("Not now.", code="not_now")

    assert (refused.status_code, refused.detail) == (409, {"detail": "Not now.", "code": "not_now"})


def test_a_command_that_sweeps_nothing_says_nothing(capsys):
    call_command("features")

    assert capsys.readouterr().out == ""


def test_timedelta_is_a_duration_setting():
    assert declarations.Setting("x", models.DurationField(), timedelta(hours=1)).type == "duration"
