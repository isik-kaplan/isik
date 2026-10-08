"""manage.py exemptions - every exemption the project declares, by the rule it skips, with where and why."""

import inspect
import json
import os
import types
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from isik.common.utils.exemptions import declared_exemptions, exemption_class
from isik.django.apps.common import exemptions as project
from isik.django.apps.common.db import constraints
from isik.django.apps.common.db.constraints import DJANGO_VALIDATORS_WITHOUT_ONE
from isik.django.apps.common.exemptions import (
    ExemptionEntry,
    ExemptionRule,
    policy_rule,
    project_exemption_rules,
    project_exemptions,
    unimported_project_exemption_types,
    unseen_project_exemptions,
)
from isik.django.apps.common.management.commands import exemptions as command
from tests.django.drf.test_coverage import Gated, GatedPlainView, SetUp
from tests.testapp import models


URLCONF = "tests.django.drf.test_coverage"
MODELS = str(Path(models.__file__).resolve())
COVERAGE = inspect.getsourcefile(Gated)
CONSTRAINTS = str(Path(constraints.__file__).resolve())
DATABASE_FORM = "isik.validators.database-form"


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
            "library": False,
        } in payload["exemptions"]
        assert payload["unseen"] == []
        assert payload["unimported_types"] == []

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
            f"{source.resolve()}:2 makes an exemption through NoHelpText that loading the project didn't - it "
            "isn't listed above.\n"
        )
        assert payload["unseen"] == [{"file": str(source.resolve()), "line": 2, "name": "NoHelpText"}]

    def test_unimported_types_are_warned_about(self, monkeypatch, tmp_path):
        source = tmp_path / "checks.py"
        source.write_text("class Unloaded(Exemption, rule='app.unloaded', why='x'):\n    pass\n")
        monkeypatch.setattr(project, "_project_paths", lambda: [str(tmp_path)])

        _, err = run("--rule", "testapp.help-text")
        payload = json.loads(run("--format", "json")[0])

        assert err == (
            f"{source.resolve()}:1 declares Unloaded, which loading the project didn't import - its exemptions "
            "aren't listed.\n"
        )
        assert payload["unimported_types"] == [{"file": str(source.resolve()), "line": 1, "name": "Unloaded"}]
        assert unimported_project_exemption_types(URLCONF) == [(str(source.resolve()), 1, "Unloaded")]

    def test_test_code_in_the_project_is_not_scanned(self, monkeypatch, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_views.py").write_text("NoHelpText(reason='a test checking a refusal')\n")
        monkeypatch.setattr(project, "_project_paths", lambda: [str(tmp_path)])

        assert unseen_project_exemptions(URLCONF) == []
        assert unseen_project_exemptions(URLCONF, exclude=()) == [
            (str((tmp_path / "tests" / "test_views.py").resolve()), 1, "NoHelpText")
        ]


def teaching_site():
    """
    The one place isik made its exemptions for Django's validators without a database form, checked to be
    the line in isik's constraints making them - found from the record rather than searched for, because
    mutmut's copy of the file holds that line once per mutant.
    """
    [(file, line)] = {(e.file, e.line) for e in declared_exemptions(DATABASE_FORM, include_library=True) if e.library}
    assert file == CONSTRAINTS
    assert "NoDatabaseForm(reason=why)" in Path(file).read_text().splitlines()[line - 1]
    return file, line


class TestALibrarysOwn:
    """isik's exemptions for Django's own validators are isik's, not the project's."""

    def test_they_are_left_out_of_the_projects_listing_and_counts(self):
        # testapp's models make some of their own, which are the project's and stay.
        projects = [e for e in project_exemptions(URLCONF) if e.rule == DATABASE_FORM]

        assert projects
        assert [e for e in projects if e.file == CONSTRAINTS or e.library] == []
        assert {rule.rule: rule.count for rule in project_exemption_rules(URLCONF)}[DATABASE_FORM] == len(projects)
        assert command._where(*teaching_site()) not in run("--rule", DATABASE_FORM)[0]

    def test_asked_for_they_are_listed_where_isik_made_them(self):
        own = [e for e in project_exemptions(URLCONF, include_library=True) if e.rule == DATABASE_FORM]

        isiks = [e for e in own if e.library]

        assert {(e.file, e.line) for e in isiks} == {teaching_site()}
        assert {e.reason for e in isiks} == set(DJANGO_VALIDATORS_WITHOUT_ONE.values())
        assert [e for e in own if not e.library] == [e for e in project_exemptions(URLCONF) if e.rule == DATABASE_FORM]
        rules = {rule.rule: rule.count for rule in project_exemption_rules(URLCONF, include_library=True)}
        assert rules[DATABASE_FORM] == len(own)

    def test_the_command_lists_them_when_asked(self):
        out, _ = run("--rule", DATABASE_FORM, "--include-library")
        listed = json.loads(run("--rule", DATABASE_FORM, "--include-library", "--format", "json")[0])["exemptions"]
        rules = json.loads(run("--rules", "--include-library", "--format", "json")[0])["rules"]

        assert f"{command._where(*teaching_site())}  " in out
        assert {(e["file"], e["line"]) for e in listed if e["library"]} == {teaching_site()}
        assert next(r["count"] for r in rules if r["rule"] == DATABASE_FORM) == len(listed)


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
        monkeypatch.setattr(command, "project_exemption_rules", lambda urlconf, include_library: [])

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
        monkeypatch.setattr(command, "project_exemptions", lambda urlconf, include_library: entries)
        monkeypatch.setattr(command, "unseen_project_exemptions", lambda urlconf: [])

        assert run() == ("a.rule  ?  why a\nb.rule  ?  list - why b\n", "")

    def test_rules_are_one_per_line_with_their_counts(self, monkeypatch):
        found = [ExemptionRule("a.rule", "Why a.", 2), ExemptionRule("bb.rule", "Why b.", 10)]
        monkeypatch.setattr(command, "project_exemption_rules", lambda urlconf, include_library: found)

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
        assert actions["urlconf"].help == (
            "A urlconf to load views from - repeat it for several. Every urlconf the project serves by default."
        )
        assert (actions["urlconf"].default, type(actions["urlconf"]).__name__) == (None, "_AppendAction")
        assert actions["include_library"].help == "Also the exemptions isik and other libraries make for themselves."
        assert (actions["include_library"].default, actions["include_library"].const) == (False, True)
        with pytest.raises(CommandError):
            run("--format", "yaml")


HOSTED = "tests.django.apps.common.hosted_urls"
HOSTED_REASON = "routed only by the api host, beside the main widget viewset"


class TestAcrossUrlconfs:
    def hosted(self, entries):
        return [
            (e.rule, e.declared_by, e.reason, e.action)
            for e in entries
            if e.reason in (HOSTED_REASON, "a host's health check reads the list")
        ]

    def test_every_django_hosts_urlconf_is_loaded_by_default(self, settings):
        settings.ROOT_URLCONF = URLCONF
        settings.ROOT_HOSTCONF = "tests.django.apps.common.hosts"

        out = StringIO()
        call_command("exemptions", "--format", "json", stdout=out, stderr=StringIO())
        listed = json.loads(out.getvalue())["exemptions"]

        assert [(e["rule"], e["declared_by"], e["action"]) for e in listed if e["reason"] == HOSTED_REASON] == [
            ("isik.viewset-registry", "ViewSetRegistryExemption", None)
        ]
        assert self.hosted(project_exemptions()) == [
            ("isik.viewset-registry", "ViewSetRegistryExemption", HOSTED_REASON, None),
            ("policy.set-up", "HostedGated", "a host's health check reads the list", "list"),
        ]

    def test_urlconf_given_twice_loads_both(self):
        out, _ = run("--urlconf", HOSTED, "--rule", "policy.set-up")

        assert out.splitlines()[-1].endswith("list - a host's health check reads the list")
        assert self.hosted(project_exemptions([URLCONF, HOSTED]))[-1] == (
            "policy.set-up",
            "HostedGated",
            "a host's health check reads the list",
            "list",
        )
        assert "HostedGated" not in {e.declared_by for e in project_exemptions(URLCONF)}


class TestWhatGetsLoaded:
    def test_every_urlconf_given_is_loaded(self, monkeypatch):
        loaded = []
        monkeypatch.setattr(
            project, "get_resolver", lambda urlconf: loaded.append(urlconf) or types.SimpleNamespace(url_patterns=[])
        )

        project._load_everything(["a.urls", "b.urls"])

        assert loaded == ["a.urls", "b.urls"]

    def test_each_listing_loads_the_urlconfs_it_was_given(self, monkeypatch, tmp_path):
        loaded = []
        monkeypatch.setattr(project, "_load_everything", loaded.append)
        monkeypatch.setattr(project, "_policy_entries", lambda urlconf: ([], {}))
        monkeypatch.setattr(project, "_project_paths", lambda: [str(tmp_path)])

        project_exemptions("a.urls")
        unseen_project_exemptions("b.urls")
        unimported_project_exemption_types("c.urls")

        assert loaded == ["a.urls", "b.urls", "c.urls"]

    def test_the_unimported_scan_skips_test_code_unless_told_otherwise(self, monkeypatch, tmp_path):
        (tmp_path / "tests").mkdir()
        source = tmp_path / "tests" / "types.py"
        source.write_text("class InATest(Exemption, rule='tmp.in-a-test', why='x'):\n    pass\n")
        monkeypatch.setattr(project, "_project_paths", lambda: [str(tmp_path)])

        assert unimported_project_exemption_types(URLCONF) == []
        assert unimported_project_exemption_types(URLCONF, exclude=()) == [(str(source.resolve()), 1, "InATest")]

    def test_the_command_scans_with_the_urlconfs_it_was_given(self, monkeypatch):
        asked = []
        monkeypatch.setattr(
            command, "unseen_project_exemptions", lambda urlconf: asked.append(("unseen", urlconf)) or []
        )
        monkeypatch.setattr(
            command, "unimported_project_exemption_types", lambda urlconf: asked.append(("unimported", urlconf)) or []
        )

        run("--rule", "testapp.help-text")

        assert asked == [("unseen", [URLCONF]), ("unimported", [URLCONF])]
