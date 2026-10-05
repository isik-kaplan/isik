"""manage.py exemptions - every exemption the project declares, by the rule it skips, with where and why."""

import inspect
import json
import os
import types
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from isik.common.utils.exemptions import exemption_class
from isik.django.apps.common import exemptions as project
from isik.django.apps.common.exemptions import (
    ExemptionEntry,
    ExemptionRule,
    policy_rule,
    project_exemption_rules,
    project_exemptions,
    unseen_project_exemptions,
)
from isik.django.apps.common.management.commands import exemptions as command
from tests.django.drf.test_coverage import Gated, GatedPlainView, SetUp
from tests.testapp import models


URLCONF = "tests.django.drf.test_coverage"
MODELS = str(Path(models.__file__).resolve())
COVERAGE = inspect.getsourcefile(Gated)


Unused = exemption_class("Unused", rule="tests.command-unused", why="Nothing here ever skips it.")
Twice = exemption_class("Twice", rule="tests.command-twice", why="Skipped twice.", min_length=1)


def run(*args):
    out, err = StringIO(), StringIO()
    call_command("exemptions", "--urlconf", URLCONF, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def help_text_line():
    """The line in testapp's models that makes its NoHelpText, read from the source."""
    return next(n for n, line in enumerate(Path(MODELS).read_text().splitlines(), 1) if "help_text=NoHelpText(" in line)


class TestTheListing:
    def test_a_policys_rule_is_named_for_it(self):
        assert policy_rule(SetUp) == "policy.set-up"

    def test_every_exemption_type_and_routed_policy_is_listed_by_rule(self):
        entries = project_exemptions(URLCONF)

        assert [e.rule for e in entries] == sorted(e.rule for e in entries)
        assert [e for e in entries if e.rule == "testapp.help-text"] == [
            ExemptionEntry(
                "testapp.help-text", "NoHelpText", "the model's own name already says what the text is", MODELS,
                help_text_line(),
            )
        ]  # fmt: skip
        assert [e for e in entries if e.rule == "policy.set-up"] == [
            ExemptionEntry(
                "policy.set-up", "Gated", "reading one changes nothing the gate protects", COVERAGE,
                inspect.getsourcelines(Gated)[1], "retrieve",
            )
        ]  # fmt: skip

    def test_a_view_routed_twice_is_listed_once(self):
        assert [e.declared_by for e in project_exemptions(URLCONF) if e.rule == "policy.set-up"] == ["Gated"]
        assert GatedPlainView.request_policy_exemptions(SetUp) == {}

    def test_each_rule_with_why_and_count(self):
        rules = {rule.rule: rule for rule in project_exemption_rules(URLCONF)}

        assert rules["testapp.help-text"] == ExemptionRule(
            "testapp.help-text", "Every field should say what it holds in help_text.", 1
        )
        assert rules["policy.set-up"] == ExemptionRule("policy.set-up", "This request isn't allowed.", 1)
        assert rules["isik.guarded-fields.writes-none"].why.startswith("A write through a viewset")

    def test_a_policy_with_a_docstring_gives_its_first_line_as_why(self, monkeypatch):
        monkeypatch.setattr(SetUp, "__doc__", "Set up\n    first.\n\nMore.")

        assert {r.rule: r.why for r in project_exemption_rules(URLCONF)}["policy.set-up"] == "Set up first."

    def test_calls_the_import_never_ran_are_found(self, monkeypatch, tmp_path):
        source = tmp_path / "later.py"
        source.write_text("def later():\n    return NoHelpText(reason='made only once later() runs')\n")
        monkeypatch.setattr(project, "_project_paths", lambda: [str(tmp_path)])

        assert unseen_project_exemptions(URLCONF) == [(str(source.resolve()), 2, "NoHelpText")]

    def test_the_projects_own_apps_are_scanned_and_isik_and_libraries_are_not(self):
        assert project._project_paths() == [str(Path(models.__file__).resolve().parent)]


class TestTheCommand:
    def test_one_rule_as_text(self):
        out, err = run("--rule", "testapp.help-text")

        where = f"{os.path.relpath(MODELS)}:{help_text_line()}"
        assert out == f"testapp.help-text  {where}  the model's own name already says what the text is\n"
        assert err == ""

    def test_a_policys_exemption_names_its_action(self):
        out, _ = run("--rule", "policy.set-up")

        assert out.rstrip("\n").endswith("retrieve - reading one changes nothing the gate protects")

    def test_an_unknown_rule_is_refused(self):
        with pytest.raises(CommandError, match=r"^No exemption type or policy exempts from 'nothing.here'\.$"):
            run("--rule", "nothing.here")

    def test_json_is_stable(self):
        first, _ = run("--format", "json")
        second, _ = run("--format", "json")
        payload = json.loads(first)

        assert first == second
        assert {
            "rule": "testapp.help-text",
            "declared_by": "NoHelpText",
            "action": None,
            "reason": "the model's own name already says what the text is",
            "file": MODELS,
            "line": help_text_line(),
        } in payload["exemptions"]
        assert payload["unseen"] == []

    def test_rules_as_text_and_json(self):
        text, _ = run("--rules")
        payload = json.loads(run("--rules", "--format", "json")[0])

        assert "testapp.help-text" in text
        assert {"rule": "policy.set-up", "why": "This request isn't allowed.", "count": 1} in payload["rules"]

    def test_unseen_calls_are_warned_about(self, monkeypatch, tmp_path):
        source = tmp_path / "later.py"
        source.write_text("def later():\n    return NoHelpText(reason='made only once later() runs')\n")
        monkeypatch.setattr(project, "_project_paths", lambda: [str(tmp_path)])

        _, err = run("--rule", "testapp.help-text")
        payload = json.loads(run("--format", "json")[0])

        assert err == (
            f"{source.resolve()}:2 makes a NoHelpText that wasn't made while loading the project - it isn't "
            "listed above.\n"
        )
        assert payload["unseen"] == [{"file": str(source.resolve()), "line": 2, "type": "NoHelpText"}]


class TestEdges:
    def test_a_class_without_source_has_no_site(self):
        assert project._source(type("Dynamic", (), {})) == (None, None)

    def test_an_exemption_without_a_site_is_shown_as_unknown(self):
        assert command._where(None, None) == "?"

    def test_a_path_outside_the_working_directory_stays_absolute(self, tmp_path):
        assert command._where(str(tmp_path / "x.py"), 3) == f"{tmp_path / 'x.py'}:3"

    def test_a_rule_without_exemptions_prints_nothing(self):
        assert run("--rule", "tests.command-unused") == ("", "")

    def test_no_rules_print_nothing(self, monkeypatch):
        monkeypatch.setattr(command, "project_exemption_rules", lambda urlconf: [])

        assert run("--rules") == ("", "")


class TestCountsAndOrder:
    def test_a_rule_counts_each_of_its_exemptions_in_the_order_made(self):
        first, second = Twice(reason="first"), Twice(reason="second")
        mine = [e for e in project_exemptions(URLCONF) if e.rule == "tests.command-twice"]

        assert [e.reason for e in mine][-2:] == ["first", "second"]
        assert [(e.file, e.line) for e in mine][-2:] == [(first.file, first.line), (second.file, second.line)]
        rules = {rule.rule: rule.count for rule in project_exemption_rules(URLCONF)}
        assert rules["tests.command-twice"] == len(mine)
        assert rules["tests.command-unused"] == 0

    def test_library_and_isik_apps_are_not_scanned(self, monkeypatch):
        configs = [
            types.SimpleNamespace(path="/venv/lib/python3.12/site-packages/allauth"),
            types.SimpleNamespace(path="/usr/lib/python3/dist-packages/other"),
            types.SimpleNamespace(path=str(Path(project.__file__).resolve().parent)),
            types.SimpleNamespace(path="/project/apps/users"),
            types.SimpleNamespace(path="/project/apps/users"),
        ]
        monkeypatch.setattr(project.apps, "get_app_configs", lambda: configs)

        assert project._project_paths() == ["/project/apps/users"]


class TestTheOutput:
    def test_columns_line_up_and_the_last_runs_on(self):
        assert command._columns([("a", "bb", "last one"), ("ccc", "d", "x")]) == [
            "a    bb  last one",
            "ccc  d   x",
        ]

    def test_a_path_under_the_working_directory_is_relative(self):
        assert command._where(str(Path.cwd() / "apps" / "x.py"), 3) == f"{Path('apps') / 'x.py'}:3"

    def test_rows_are_one_per_line(self, monkeypatch):
        entries = [
            ExemptionEntry("a.rule", "A", "why a", None, None),
            ExemptionEntry("b.rule", "B", "why b", None, None, "list"),
        ]
        monkeypatch.setattr(command, "project_exemptions", lambda urlconf: entries)
        monkeypatch.setattr(command, "unseen_project_exemptions", lambda urlconf: [])

        assert run() == ("a.rule  ?  why a\nb.rule  ?  list - why b\n", "")

    def test_rules_are_one_per_line_with_their_counts(self, monkeypatch):
        found = [ExemptionRule("a.rule", "Why a.", 2), ExemptionRule("bb.rule", "Why b.", 10)]
        monkeypatch.setattr(command, "project_exemption_rules", lambda urlconf: found)

        assert run("--rules") == ("a.rule   2   Why a.\nbb.rule  10  Why b.\n", "")

    def test_json_is_indented_by_two(self):
        out, _ = run("--format", "json")
        rules, _ = run("--rules", "--format", "json")

        assert out == json.dumps(json.loads(out), indent=2) + "\n"
        assert rules == json.dumps(json.loads(rules), indent=2) + "\n"

    def test_the_options(self):
        parser = command.Command().create_parser("manage.py", "exemptions")
        actions = {action.dest: action for action in parser._actions}

        assert actions["rule"].help == "Only this rule's exemptions."
        assert actions["rules"].help == "Each rule, why it's there, and its count."
        assert (actions["rules"].default, actions["rules"].const) == (False, True)
        assert (actions["format"].choices, actions["format"].default) == (["text", "json"], "text")
        assert actions["urlconf"].help == "The urlconf to load views from (ROOT_URLCONF by default)."
        with pytest.raises(CommandError):
            run("--format", "yaml")
