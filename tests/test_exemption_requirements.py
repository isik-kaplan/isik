"""
How much each rule asks of an opt-out - strict, optional or off - set by the rule's owner and overridden
by the project, for isik's rules and a project's own alike.
"""

import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from isik.common.settings import Constant, NoEnv, NoOption, NoQuestion, Setting, Settings
from isik.common.utils.exemptions import (
    Exemption,
    Requirement,
    declared_exemptions,
    enforced,
    exemption_class,
    exemption_requirements,
    opted_out,
    requirement_of,
)
from isik.django.apps.common import checks
from isik.django.apps.common.db import constraints
from isik.django.drf.viewsets import NoPrecondition, ViewSetRegistryExemption, ViewSetRegistryMixin
from isik.django.drf.viewsets.preconditions import PreconditionMixin
from tests.testapp.models import Widget


REASON = "the value is chosen once when the service is installed, not per run"
# Where this module's code says it is - under mutmut, a copied test's cached bytecode can name the
# original file rather than __file__.
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())


def declare():
    """
    The types these tests share - made again for every test (see `_no_levels`), since mutmut credits code
    to the tests that ran it, and what runs at import is credited to whichever test runs first.
    """
    FooReason = exemption_class("FooReason", rule="tests.foo", why="Every foo says why it skips bar.")
    LooseReason = exemption_class(
        "LooseReason", rule="tests.loose", why="Every loose one says why.", requirement=Requirement.OPTIONAL
    )

    class KindOfFoo(FooReason, rule="tests.foo.kind", why="A kind of foo, under foo's level."):
        pass

    return FooReason, LooseReason, KindOfFoo


FooReason, LooseReason, KindOfFoo = declare()


@pytest.fixture(autouse=True)
def _no_levels(monkeypatch):
    for declared in declare():
        monkeypatch.setitem(globals(), declared.__name__, declared)
    exemption_requirements({})
    yield
    exemption_requirements({})


class TestLevels:
    def test_a_rule_is_strict_unless_its_owner_says_otherwise(self):
        assert requirement_of(FooReason) is Requirement.STRICT
        assert requirement_of(LooseReason) is Requirement.OPTIONAL

    def test_strict_needs_a_reason_over_the_floor(self):
        with pytest.raises(TypeError, match=r"^FooReason needs reason=\.$"):
            FooReason()
        with pytest.raises(ValueError, match=r"^FooReason needs a reason of at least 40 characters"):
            FooReason(reason="")

    def test_optional_takes_no_reason_but_a_given_one_still_says_something(self):
        assert LooseReason().reason == ""
        assert LooseReason(reason="  ").reason == ""
        with pytest.raises(ValueError, match=r"^LooseReason needs a reason of at least 40 characters"):
            LooseReason(reason="short")

    def test_a_project_sets_a_rules_level(self):
        exemption_requirements({"tests.foo": Requirement.OPTIONAL})

        assert requirement_of(FooReason) is Requirement.OPTIONAL
        assert FooReason().reason == ""

    def test_a_kind_of_rule_takes_its_parents_level_unless_it_has_its_own(self):
        exemption_requirements({"tests.foo": Requirement.OFF})
        assert requirement_of(KindOfFoo) is Requirement.OFF

        exemption_requirements({"tests.foo": Requirement.OFF, "tests.foo.kind": Requirement.STRICT})
        assert requirement_of(KindOfFoo) is Requirement.STRICT

    def test_a_call_replaces_the_last(self):
        exemption_requirements({"tests.foo": Requirement.OPTIONAL})
        exemption_requirements({"tests.loose": Requirement.STRICT})

        assert requirement_of(FooReason) is Requirement.STRICT
        assert requirement_of(LooseReason) is Requirement.STRICT

    def test_the_django_setting_and_a_call_over_it(self):
        with override_settings(ISIK_EXEMPTIONS={"tests.foo": Requirement.OPTIONAL}):
            assert requirement_of(FooReason) is Requirement.OPTIONAL
            exemption_requirements({"tests.foo": Requirement.OFF})
            assert requirement_of(FooReason) is Requirement.OFF

    @pytest.mark.parametrize("where", ["call", "setting"])
    def test_a_level_is_a_requirement_never_a_string(self, where):
        if where == "call":
            with pytest.raises(TypeError) as raised:
                exemption_requirements({"tests.foo": "optional"})
            assert str(raised.value) == (
                "exemption_requirements() gives 'tests.foo' the level 'optional' - write a Requirement, "
                "e.g. Requirement.OPTIONAL."
            )
        else:
            with override_settings(ISIK_EXEMPTIONS={"tests.foo": "off"}), pytest.raises(TypeError) as raised:
                requirement_of(FooReason)
            assert str(raised.value).startswith("ISIK_EXEMPTIONS gives 'tests.foo' the level 'off'")

    def test_an_owner_gives_a_requirement(self):
        with pytest.raises(TypeError, match=r"^Bad's requirement must be a Requirement, not 'optional'\.$"):
            exemption_class("Bad", rule="tests.bad", why="x", requirement="optional")

    def test_off_is_for_a_check_never_for_what_changes_behavior(self):
        exemption_requirements({"tests.foo": Requirement.OFF, "settings.question": Requirement.OFF})

        assert enforced(FooReason) is False
        assert enforced(LooseReason) is True
        with pytest.raises(TypeError) as raised:
            requirement_of(NoQuestion)
        assert str(raised.value) == (
            "settings.question can't be Requirement.OFF: its exemption changes what code does rather than "
            "answering a check. Make it Requirement.OPTIONAL instead."
        )

    def test_changes_behavior_is_inherited_and_can_be_said(self):
        Behaving = exemption_class("Behaving", rule="tests.behaving", why="x", changes_behavior=True)

        class Kind(Behaving, rule="tests.behaving.kind", why="y"):
            pass

        assert (Behaving.changes_behavior, Kind.changes_behavior, FooReason.changes_behavior) == (True, True, False)


class TestOptedOut:
    def test_an_exemption_is_itself(self):
        reason = FooReason(reason=REASON)

        assert opted_out(reason, FooReason, plain=True) is reason

    def test_the_plain_value_only_where_the_rule_isnt_strict(self):
        assert opted_out(True, FooReason, plain=True) is None

        exemption_requirements({"tests.foo": Requirement.OPTIONAL})
        made = opted_out(True, FooReason, plain=True)

        assert isinstance(made, FooReason) and made.reason == ""
        assert made in declared_exemptions("tests.foo")
        assert made.file == HERE

    def test_anything_else_is_none(self):
        exemption_requirements({"tests.foo": Requirement.OPTIONAL})

        assert opted_out(False, FooReason, plain=True) is None
        assert opted_out("why", FooReason, plain=True) is None
        assert opted_out(1, FooReason, plain=True) is None


class TestSettings:
    def test_false_needs_an_optional_rule(self):
        with pytest.raises(TypeError) as raised:
            Setting(str, "Name", default="", question=False)
        assert str(raised.value) == (
            "False opts out of settings.question without a reason, which needs Requirement.OPTIONAL for it - "
            "or write NoQuestion(reason=...)."
        )

    def test_false_where_its_optional(self):
        exemption_requirements(
            {
                "settings.question": Requirement.OPTIONAL,
                "settings.option": Requirement.OPTIONAL,
                "settings.environment-variable": Requirement.OPTIONAL,
                "settings.constant": Requirement.OPTIONAL,
            }
        )
        setting = Setting(str, "Name", default="", question=False, option=False, env=False)

        assert isinstance(setting.question, NoQuestion) and not setting.asked
        assert isinstance(setting.option, NoOption) and isinstance(setting.env, NoEnv)

        class Quiet(Settings, env=False):
            name = Setting(str, "Name", default="")
            fixed = Constant(32)

        assert isinstance(Quiet.settings_options()["env"], NoEnv)
        assert Quiet.from_env({"NAME": "x"}).name == ""

    def test_a_constant_without_a_reason_needs_an_optional_rule(self):
        with pytest.raises(TypeError, match=r"^IsConstant needs reason=\.$"):
            Constant(32)


class TestDjango:
    def test_a_registry_exemption_as_true(self):
        with pytest.raises(ImproperlyConfigured):

            class Strict(ViewSetRegistryMixin):
                model = Widget
                exempt_from_registry = True

        exemption_requirements({"isik.viewset-registry": Requirement.OPTIONAL})

        class Loose(ViewSetRegistryMixin):
            model = Widget
            exempt_from_registry = True

        assert isinstance(Loose.exempt_from_registry, ViewSetRegistryExemption)
        assert ViewSetRegistryMixin.get_for_model(Widget) is not Loose

    def test_a_map_of_exemptions_with_true(self):
        exemption_requirements({"isik.precondition": Requirement.OPTIONAL})

        class Loose(PreconditionMixin):
            precondition_exempt_actions = {"poke": True}

        assert isinstance(Loose.precondition_exempt_actions["poke"], NoPrecondition)

    def test_the_validators_check_asks_nothing_when_its_rule_is_off(self, monkeypatch):
        monkeypatch.setattr(constraints, "unclassified_validators", lambda: ["app.Thing.note (unanswered)"])
        assert len(checks.every_validator_says_whether_it_can_hold_at_the_column(app_configs=None)) == 1

        exemption_requirements({"isik.validators.database-form": Requirement.OFF})

        assert checks.every_validator_says_whether_it_can_hold_at_the_column(app_configs=None) == []


def test_exemption_is_still_the_base():
    assert issubclass(FooReason, Exemption)


def test_a_reasonless_exemption_still_reads_as_one():
    Shown = exemption_class(
        "ShownAsEmpty", rule="tests.requirements.shown-as-empty", why="x", shows_as="", requirement=Requirement.OPTIONAL
    )

    assert bool(LooseReason()) is True
    assert bool(LooseReason(reason=REASON)) is True
    assert bool(Shown()) is False and Shown() == ""


class TestWithoutDjango:
    def test_without_django_only_the_call_counts(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "django.conf", None)  # importing it now fails

        assert requirement_of(FooReason) is Requirement.STRICT
        exemption_requirements({"tests.foo": Requirement.OPTIONAL})
        assert requirement_of(FooReason) is Requirement.OPTIONAL

    def test_unconfigured_django_settings_are_left_alone(self, monkeypatch):
        from django.conf import LazySettings

        monkeypatch.setattr(LazySettings, "configured", property(lambda self: False))

        with override_settings(ISIK_EXEMPTIONS={"tests.foo": Requirement.OFF}):
            assert requirement_of(FooReason) is Requirement.STRICT


def test_a_type_that_shows_as_something_is_as_true_as_that():
    ShowsAsText = exemption_class("ShowsAsText", rule="tests.requirements.shows-as-text", why="x", shows_as="kept")

    assert bool(ShowsAsText(reason=REASON)) is True


def test_a_policy_a_view_doesnt_hold_exempts_nothing():
    from isik.django.drf.viewsets import RequestPoliciesMixin, RequestPolicy

    class Unheld(RequestPolicy):
        pass

    class View(RequestPoliciesMixin):
        request_policies = []

    assert View.request_policy_exemptions(Unheld) == {}
