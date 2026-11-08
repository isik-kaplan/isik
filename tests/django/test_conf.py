"""ISIK, the one setting isik reads - and the helpers reading, overriding and checking it."""

import unittest

import pytest
from django.conf import settings as django_settings
from django.test import SimpleTestCase

from isik.common.utils.exemptions import Requirement
from isik.django import conf
from isik.django.apps.common.checks import isik_is_configured
from isik.django.conf import (
    configured,
    django_area,
    django_setting,
    give_swappable_settings,
    isik_areas,
    isik_with,
    override_isik,
    swappable_keys,
)


@pytest.fixture
def isik(settings):
    settings.ISIK = {
        "EXEMPTIONS": {"tests.a": Requirement.OPTIONAL},
        "DJANGO": {"NOTIFICATIONS": {"PROJECT_NAME": "Acme", "MAX_ATTEMPTS": 5}, "PRIMARY_KEY": "uuid.uuid4"},
    }
    return settings


class TestReading:
    def test_whats_set(self, isik):
        assert configured()["EXEMPTIONS"] == {"tests.a": Requirement.OPTIONAL}
        assert django_area("PRIMARY_KEY") == "uuid.uuid4"

    def test_an_area_not_set_is_the_default(self, isik):
        assert django_area("SMS") is None
        assert django_area("SMS", {}) == {}

    @pytest.mark.parametrize("value", [None, ["DJANGO"], "DJANGO"])
    def test_isik_that_isnt_a_dict_is_nothing_set(self, settings, value):
        settings.ISIK = value

        assert configured() == {}
        assert django_area("CHECKS", "unset") == "unset"

    def test_no_isik_is_nothing_set(self, settings):
        del settings.ISIK

        assert configured() == {}

    def test_a_setting_in_an_area(self, settings):
        settings.ISIK = {"DJANGO": {"DRF": {"PAGINATION_MAX_PAGE_SIZE": 5}, "HISTORY": None}}

        assert django_setting("DRF", "PAGINATION_MAX_PAGE_SIZE") == 5
        assert django_setting("DRF", "OTHER") is None
        assert django_setting("DRF", "OTHER", 1) == 1
        assert django_setting("SESSIONS", "HEADER_NAME", "x") == "x"
        assert django_setting("HISTORY", "CONTEXT_KEYS", ()) == ()

    def test_django_that_isnt_a_dict_is_no_area_set(self, settings):
        settings.ISIK = {"DJANGO": ["CHECKS"]}

        assert django_area("CHECKS", "unset") == "unset"


class TestIsikWith:
    def test_an_areas_key_replaced_and_the_rest_kept(self, isik):
        assert isik_with(DJANGO={"NOTIFICATIONS": {"MAX_ATTEMPTS": 1}}) == {
            "EXEMPTIONS": {"tests.a": Requirement.OPTIONAL},
            "DJANGO": {"NOTIFICATIONS": {"PROJECT_NAME": "Acme", "MAX_ATTEMPTS": 1}, "PRIMARY_KEY": "uuid.uuid4"},
        }

    def test_a_keys_value_replaced_whole(self, isik):
        isik.ISIK["DJANGO"]["NOTIFICATIONS"]["PUSH"] = {"APNS": {"TEAM_ID": "T"}, "FCM": {"CREDENTIALS": "c"}}

        changed = isik_with(DJANGO={"NOTIFICATIONS": {"PUSH": {"FCM": {"CREDENTIALS": "d"}}}})

        assert changed["DJANGO"]["NOTIFICATIONS"]["PUSH"] == {"FCM": {"CREDENTIALS": "d"}}

    def test_an_area_that_isnt_a_dict_replaced(self, isik):
        assert isik_with(DJANGO={"PRIMARY_KEY": "x.y"})["DJANGO"]["PRIMARY_KEY"] == "x.y"

    def test_an_area_not_set_added(self, isik):
        changed = isik_with(DJANGO={"SMS": {"default": {"BACKEND": "b"}}})

        assert changed["DJANGO"]["SMS"] == {"default": {"BACKEND": "b"}}
        assert changed["DJANGO"]["NOTIFICATIONS"]["PROJECT_NAME"] == "Acme"

    def test_an_exemptions_rule_replaced_and_the_rest_kept(self, isik):
        changed = isik_with(EXEMPTIONS={"tests.b": Requirement.OFF})

        assert changed["EXEMPTIONS"] == {"tests.a": Requirement.OPTIONAL, "tests.b": Requirement.OFF}

    def test_nothing_set_yet(self, settings):
        del settings.ISIK

        assert isik_with(DJANGO={"CHECKS": {}}) == {"DJANGO": {"CHECKS": {}}}

    def test_leaves_isik_as_it_was(self, isik):
        isik_with(DJANGO={"NOTIFICATIONS": {"MAX_ATTEMPTS": 1}})

        assert django_area("NOTIFICATIONS")["MAX_ATTEMPTS"] == 5


class TestOverrideIsik:
    def test_as_a_context_manager(self, isik):
        with override_isik(DJANGO={"NOTIFICATIONS": {"MAX_ATTEMPTS": 1}}):
            assert django_area("NOTIFICATIONS") == {"PROJECT_NAME": "Acme", "MAX_ATTEMPTS": 1}

        assert django_area("NOTIFICATIONS")["MAX_ATTEMPTS"] == 5

    def test_over_what_isik_is_when_entered(self, isik):
        overriding = override_isik(DJANGO={"PRIMARY_KEY": "x.y"})
        isik.ISIK = {"DJANGO": {"SCHEMA_LANGUAGE": "fr"}}

        with overriding:
            assert configured() == {"DJANGO": {"SCHEMA_LANGUAGE": "fr", "PRIMARY_KEY": "x.y"}}

    def test_as_a_decorator(self, isik):
        @override_isik(DJANGO={"SCHEMA_LANGUAGE": "fr"})
        def language():
            return django_area("SCHEMA_LANGUAGE")

        assert language() == "fr"
        assert django_area("SCHEMA_LANGUAGE") is None

    def test_on_a_test_case(self):
        @override_isik(DJANGO={"SCHEMA_LANGUAGE": "fr"})
        class Overridden(SimpleTestCase):
            def test_language(self):
                assert django_area("SCHEMA_LANGUAGE") == "fr"
                assert django_area("FEATURES")["SETTINGS"] == "tests.testapp.feature_settings"

        result = unittest.TestResult()
        unittest.TestSuite([Overridden("test_language")]).run(result)

        assert (result.errors, result.failures, result.testsRun) == ([], [], 1)


class TestIsikAreas:
    def test_reads_an_area(self, isik):
        assert isik_areas(isik).NOTIFICATIONS == {"PROJECT_NAME": "Acme", "MAX_ATTEMPTS": 5}
        assert isik_areas(isik).EXEMPTIONS == {"tests.a": Requirement.OPTIONAL}

    @pytest.mark.parametrize("name", ["SMS", "DJANGO", "SOMETHING"])
    def test_an_area_not_set_or_not_there_isnt_read(self, isik, name):
        with pytest.raises(AttributeError, match=name):
            getattr(isik_areas(isik), name)

    def test_sets_an_area_whole(self, isik):
        isik_areas(isik).NOTIFICATIONS = {"MAX_ATTEMPTS": 1}

        assert isik.ISIK == {
            "EXEMPTIONS": {"tests.a": Requirement.OPTIONAL},
            "DJANGO": {"NOTIFICATIONS": {"MAX_ATTEMPTS": 1}, "PRIMARY_KEY": "uuid.uuid4"},
        }

    def test_sets_exemptions(self, isik):
        isik_areas(isik).EXEMPTIONS = {"tests.b": Requirement.OFF}

        assert configured()["EXEMPTIONS"] == {"tests.b": Requirement.OFF}

    def test_deletes_an_area(self, isik):
        del isik_areas(isik).NOTIFICATIONS
        del isik_areas(isik).EXEMPTIONS

        assert configured() == {"DJANGO": {"PRIMARY_KEY": "uuid.uuid4"}}

    def test_sets_where_nothing_was(self, settings):
        del settings.ISIK

        isik_areas(settings).CHECKS = {}

        assert configured() == {"DJANGO": {"CHECKS": {}}}

    def test_sets_where_django_isnt_a_dict(self, settings):
        settings.ISIK = {"DJANGO": None}

        isik_areas(settings).CHECKS = {}

        assert configured() == {"DJANGO": {"CHECKS": {}}}

    def test_leaves_what_it_read_alone(self, isik):
        before = isik.ISIK

        isik_areas(isik).PRIMARY_KEY = "x.y"

        assert before["DJANGO"]["PRIMARY_KEY"] == "uuid.uuid4"

    @pytest.mark.parametrize("name", ["DJANGO", "SOMETHING"])
    def test_nothing_but_an_area_is_set(self, isik, name):
        with pytest.raises(AttributeError, match=name):
            setattr(isik_areas(isik), name, {})


class TestSwappableSettings:
    NAMES = ("ISIK_TESTS_THING_MODEL", "ISIK_TESTS_OTHER_MODEL")

    @pytest.fixture(autouse=True)
    def registry(self, monkeypatch):
        monkeypatch.setattr(conf, "SWAPPABLE_SETTINGS", {})
        monkeypatch.setattr(conf, "DJANGO_AREAS", (*conf.DJANGO_AREAS, "TESTS"))
        yield
        for name in self.NAMES:
            if hasattr(django_settings, name):
                delattr(django_settings, name)

    def test_gives_django_what_isik_names(self, settings):
        settings.ISIK = {"DJANGO": {"TESTS": {"THING_MODEL": "app.Thing"}}}

        give_swappable_settings("TESTS", self.NAMES)

        assert django_settings.ISIK_TESTS_THING_MODEL == "app.Thing"
        assert not hasattr(django_settings, "ISIK_TESTS_OTHER_MODEL")
        assert conf.SWAPPABLE_SETTINGS == {
            "ISIK_TESTS_THING_MODEL": ("TESTS", "THING_MODEL"),
            "ISIK_TESTS_OTHER_MODEL": ("TESTS", "OTHER_MODEL"),
        }

    def test_keeps_what_the_project_set_itself(self, settings):
        settings.ISIK = {"DJANGO": {"TESTS": {"THING_MODEL": "app.Thing"}}}
        django_settings.ISIK_TESTS_THING_MODEL = "app.Mine"

        give_swappable_settings("TESTS", self.NAMES)

        assert django_settings.ISIK_TESTS_THING_MODEL == "app.Mine"

    def test_a_disagreement_is_an_error(self, settings):
        settings.ISIK = {"DJANGO": {"TESTS": {"THING_MODEL": "app.Thing", "OTHER_MODEL": "app.Other"}}}
        django_settings.ISIK_TESTS_THING_MODEL = "app.Mine"
        give_swappable_settings("TESTS", self.NAMES)

        ((error,),) = [[error for error in isik_is_configured(None) if error.id == "isik.E023"]]

        assert error.msg == (
            "ISIK_TESTS_THING_MODEL is 'app.Mine', and ISIK['DJANGO']['TESTS']['THING_MODEL'] is 'app.Thing' - "
            "name the model in ISIK alone."
        )
        assert error.hint == "See isik.django.conf."

    def test_agreeing_is_no_error(self, settings):
        settings.ISIK = {"DJANGO": {"TESTS": {"THING_MODEL": "app.Thing"}}}
        give_swappable_settings("TESTS", self.NAMES)

        assert isik_is_configured(None) == []

    def test_the_keys_named(self):
        assert swappable_keys(self.NAMES, "TESTS") == {"THING_MODEL", "OTHER_MODEL"}


class TestIsikIsConfigured:
    def messages(self):
        errors = isik_is_configured(None)
        assert {(error.id, error.hint) for error in errors} <= {("isik.E022", "See isik.django.conf.")}
        return [error.msg for error in errors]

    def test_the_test_projects_isik(self):
        assert self.messages() == []

    def test_no_isik(self, settings):
        del settings.ISIK

        assert self.messages() == []

    def test_isik_that_isnt_a_dict(self, settings):
        settings.ISIK = ["DJANGO"]

        assert self.messages() == ["ISIK is ['DJANGO'], not a dict."]

    def test_a_key_like_one_it_has(self, settings):
        settings.ISIK = {"EXEMPTION": {}, "DJANGO": {"NOTIFICATION": {}}}

        assert self.messages() == [
            "ISIK names 'EXEMPTION' - did you mean 'EXEMPTIONS'?",
            "ISIK['DJANGO'] names 'NOTIFICATION' - did you mean 'NOTIFICATIONS'?",
        ]

    def test_a_key_like_none_it_has(self, settings):
        settings.ISIK = {"ZZZ": {}, "DJANGO": {"QQQ": {}}}

        assert self.messages() == [
            "ISIK names 'ZZZ', which isn't one of EXEMPTIONS, DJANGO.",
            "ISIK['DJANGO'] names 'QQQ', which isn't one of CHECKS, PRIMARY_KEY, SCHEMA_LANGUAGE, FEATURES, "
            "NOTIFICATIONS, SMS, HISTORY, SESSIONS, IDEMPOTENCY, DRF, FEEDBACK, TEMPLATED_FIELDS.",
        ]

    def test_every_key_is_looked_at(self, settings):
        settings.ISIK = {"DJANGO": {"CHECKS": {}, "QQQ": {}}, "ZZZ": {}}

        assert self.messages() == [
            "ISIK names 'ZZZ', which isn't one of EXEMPTIONS, DJANGO.",
            "ISIK['DJANGO'] names 'QQQ', which isn't one of CHECKS, PRIMARY_KEY, SCHEMA_LANGUAGE, FEATURES, "
            "NOTIFICATIONS, SMS, HISTORY, SESSIONS, IDEMPOTENCY, DRF, FEEDBACK, TEMPLATED_FIELDS.",
        ]

    def test_a_key_that_isnt_a_string(self, settings):
        settings.ISIK = {1: {}}

        assert self.messages() == ["ISIK names 1, which isn't one of EXEMPTIONS, DJANGO."]

    def test_a_key_in_an_area_it_checks(self, settings):
        settings.ISIK = {"DJANGO": {"IDEMPOTENCY": {"LOCK_TIMOUT": 5, "CLAIM_MODEL": "a.B"}, "DRF": {"zzz": 1}}}

        assert self.messages() == [
            "ISIK['DJANGO']['IDEMPOTENCY'] names 'LOCK_TIMOUT' - did you mean 'LOCK_TIMEOUT'?",
            "ISIK['DJANGO']['DRF'] names 'zzz', which isn't one of PAGINATION_MAX_PAGE_SIZE.",
        ]

    def test_an_area_it_checks_that_isnt_a_dict(self, settings):
        settings.ISIK = {"DJANGO": {"HISTORY": ["CONTEXT_KEYS"]}}

        assert self.messages() == ["ISIK['DJANGO']['HISTORY'] is ['CONTEXT_KEYS'], not a dict."]

    def test_django_that_isnt_a_dict(self, settings):
        settings.ISIK = {"EXEMPTONS": {}, "DJANGO": "NOTIFICATIONS"}

        assert self.messages() == [
            "ISIK names 'EXEMPTONS' - did you mean 'EXEMPTIONS'?",
            "ISIK['DJANGO'] is 'NOTIFICATIONS', not a dict.",
        ]


class TestEachAppGivesItsSwappableSettings:
    @pytest.mark.parametrize(
        ("module", "area", "key", "setting"),
        [
            ("isik.django.apps.features.swapping", "FEATURES", "GRANT_MODEL", "ISIK_FEATURES_GRANT_MODEL"),
            (
                "isik.django.apps.notifications.swapping",
                "NOTIFICATIONS",
                "NOTICE_MODEL",
                "ISIK_NOTIFICATIONS_NOTICE_MODEL",
            ),
            (
                "isik.django.apps.notifications.audience.swapping",
                "NOTIFICATIONS",
                "DEVICE_MODEL",
                "ISIK_NOTIFICATIONS_DEVICE_MODEL",
            ),
        ],
    )
    def test_from_isik(self, settings, monkeypatch, module, area, key, setting):
        import importlib

        swapping = importlib.import_module(module)
        isik_areas(settings).__setattr__(area, {**getattr(isik_areas(settings), area, {}), key: "elsewhere.Model"})
        monkeypatch.delattr(django_settings, setting)
        monkeypatch.setattr(conf, "SWAPPABLE_SETTINGS", {})
        try:
            swapping.set_defaults()

            assert getattr(django_settings, setting) == "elsewhere.Model"
            assert conf.SWAPPABLE_SETTINGS[setting] == (area, key)
        finally:
            delattr(django_settings, setting)
