"""`isik` - isik's own commands: `isik exemptions` and `isik check public_names`."""

import json
import os
import sys

import pytest
from click.testing import CliRunner

from isik.cli import main


PROJECT = "tests.cli.exempting_project.policies"
# Where it is: the top-level package an import scans by default is the whole of tests/.
SCANNED = os.path.join(os.path.dirname(__file__), "exempting_project")
WHY = "The nightly export writes only counts, which name no one."


def run(*args, env=None):
    result = CliRunner().invoke(main.isik, list(args), env=env)
    return result.exit_code, result.stdout, result.stderr


@pytest.fixture
def no_django(monkeypatch):
    monkeypatch.delenv(main.SETTINGS, raising=False)


class TestExemptionsWithoutDjango:
    def test_what_the_imported_modules_make(self, no_django):
        code, out, err = run("exemptions", "--import", PROJECT, "--path", SCANNED)

        assert (code, err) == (0, "")
        # Everything made in this process is listed - the test project's own too, which other tests loaded.
        [mine] = [line for line in out.splitlines() if line.startswith("exempting-project.audit")]
        assert mine.endswith(WHY)

    def test_its_rules(self, no_django):
        code, out, _err = run("exemptions", "--import", PROJECT, "--rules", "--format", "json")

        rules = {each["rule"]: each for each in json.loads(out)["rules"]}
        assert code == 0
        assert rules["exempting-project.audit"] == {
            "rule": "exempting-project.audit",
            "why": "Every export is audited, so a reader can trace it.",
            "count": 1,
        }

    def test_one_rule(self, no_django):
        code, out, _err = run("exemptions", "--import", PROJECT, "--path", SCANNED, "--rule", "exempting-project.audit")

        assert code == 0
        assert out.startswith("exempting-project.audit")

    def test_a_rule_nothing_exempts_from(self, no_django):
        code, _out, err = run("exemptions", "--import", PROJECT, "--rule", "nothing.here")

        assert code == 1
        assert err == "Error: No exemption type or policy exempts from 'nothing.here'.\n"

    def test_a_call_loading_missed_fails_the_listing(self, no_django, tmp_path):
        (tmp_path / "late.py").write_text(
            "from tests.cli.exempting_project.policies import NoAudit\n\n"
            "def later():\n    return NoAudit(reason='Made only when called, so loading never makes it.')\n"
        )

        code, _out, err = run("exemptions", "--import", PROJECT, "--path", str(tmp_path))

        assert code == 1
        assert "makes an exemption through NoAudit that loading the project didn't" in err
        assert err.endswith("Error: 1 exemption call(s) or type(s) weren't loaded, so the listing isn't complete.\n")

    def test_the_imported_packages_are_scanned_unless_paths_are_given(self):
        import tests.cli.exempting_project.policies as policies

        assert main.package_path(policies) == os.path.dirname(os.path.dirname(os.path.dirname(policies.__file__)))

    def test_the_librarys_own_only_when_asked(self, no_django):
        _code, mine, _err = run("exemptions", "--import", PROJECT, "--format", "json")
        _code, every, _err = run("exemptions", "--import", PROJECT, "--format", "json", "--include-library")

        assert {each["library"] for each in json.loads(mine)["exemptions"]} == {False}
        assert len(json.loads(every)["exemptions"]) >= len(json.loads(mine)["exemptions"])


class TestWithDjango:
    def test_exemptions_are_manage_pys(self, monkeypatch):
        from django.core.management import call_command

        monkeypatch.setenv(main.SETTINGS, "tests.django_settings")
        code, out, _err = run("exemptions", "--rules")

        from io import StringIO

        expected = StringIO()
        call_command("exemptions", "--rules", stdout=expected)
        assert (code, out) == (0, expected.getvalue())

    def test_a_rule_nothing_exempts_from(self, monkeypatch):
        monkeypatch.setenv(main.SETTINGS, "tests.django_settings")

        code, _out, err = run("exemptions", "--rule", "nothing.here", "--urlconf", "tests.django.apps.inbound.urls")

        assert (code, err) == (1, "Error: No exemption type or policy exempts from 'nothing.here'.\n")

    def test_public_names(self, no_django):
        code, out, _err = run("check", "public_names", "--settings", "tests.django_settings", "--all", "--defaults")

        assert code == 0
        assert [line.split()[0] for line in out.splitlines()] == [
            "ISIK['DJANGO']['SYNC']['PROTOCOL_HEADER']",
            "ISIK['DJANGO']['HISTORY']['REQUEST_ID_HEADER']",
            "ISIK['DJANGO']['SESSIONS']['DEAD_SESSION_HEADER']",
        ]
        assert os.environ[main.SETTINGS] == "tests.django_settings"

    def test_public_names_need_a_django_project(self, no_django):
        code, _out, err = run("check", "public_names")

        assert (code, err) == (
            1,
            "Error: isik check public_names reads a Django project's settings - set DJANGO_SETTINGS_MODULE.\n",
        )


class TestLoadingDjango:
    def test_untouched_when_nothing_names_settings(self, no_django, monkeypatch):
        import django

        monkeypatch.setattr(django, "setup", lambda: pytest.fail("not asked for"))

        assert main.load_django(None) is False

    def test_the_settings_given_win(self, monkeypatch):
        import django

        monkeypatch.setenv(main.SETTINGS, "elsewhere")
        monkeypatch.setattr(django, "setup", lambda: None)

        assert main.load_django("tests.django_settings") is True
        assert os.environ[main.SETTINGS] == "tests.django_settings"


class TestTheWorkingDirectory:
    def test_put_first(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", ["/elsewhere", str(tmp_path)])

        main.importable_here()

        assert sys.path == [str(tmp_path), "/elsewhere", str(tmp_path)]

    def test_left_alone_when_first_already(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", [str(tmp_path), "/elsewhere"])

        main.importable_here()

        assert sys.path == [str(tmp_path), "/elsewhere"]


def test_nothing_to_list_writes_nothing(capsys):
    main.emit("", [], None)

    assert capsys.readouterr() == ("", "")
