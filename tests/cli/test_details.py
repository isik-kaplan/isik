"""The finer points of a command's options, prompts and checks - each one something a change could quietly break."""

import contextlib
import io
import threading
from enum import Enum
from pathlib import Path

import click
import pytest
import questionary
from click.testing import CliRunner

from isik.cli import HelpFormat, no_option_given, options
from isik.cli.commands import _last
from isik.cli.policies import TerminalUnlessNoInput
from isik.cli.testing import answers, assert_docs_current, assert_reachable, questionary_io, unreachable
from isik.common.settings import NoEnv, Prompt, Selector, Setting, Settings, Source


REASON = "the value is chosen once when the service is installed, not per run"


class Mode(Enum):
    FAST = "fast"
    SLOW = "slow"


def declare():
    """Declared per test, as in test_cli.py - so mutmut credits class creation to the tests that use it."""

    class Fast(Settings):
        size = Setting(int, "Size", default=1)

    class Slow(Settings):
        depth = Setting(int, "Depth", default=2)

    class Picks(Settings, prefix="PICK_"):
        mode = Selector("Mode", {"fast": Fast, "slow": Slow}, default="fast")
        mode_settings = mode.settings
        name = Setting(str, "Name", default="n")

    return Fast, Slow, Picks


Fast, Slow, Picks = declare()


@pytest.fixture(autouse=True)
def _declared(monkeypatch):
    for declared in declare():
        monkeypatch.setitem(globals(), declared.__name__, declared)


class Recording:
    adds_no_input = False

    def __init__(self):
        self.given = []

    def __call__(self, given, no_input):
        self.given.append(dict(given))
        return False


def command(settings_class, **keywords):
    keywords.setdefault("interactive", no_option_given())

    @options(settings_class, **keywords)
    @click.command()
    def main(settings):
        click.echo(repr(settings))
        click.echo(repr({name: source.value for name, source in settings._sources.items()}))

    return main


def invoke(main, args=(), **keywords):
    return CliRunner().invoke(main, list(args), **keywords)


class TestOptions:
    def test_a_casters_error_is_clicks_own_for_the_option(self):
        def even(text):
            if int(text) % 2:
                raise ValueError("must be even")
            return int(text)

        class Even(Settings):
            size = Setting(even, "Size", default=2)

        result = invoke(command(Even), ["--size", "3"])

        assert "Error: Invalid value for '--size': must be even\n" in result.output
        assert "Usage: main [OPTIONS]" in result.output

    def test_a_path_option_is_a_path(self):
        class Where(Settings):
            target = Setting(Path, "Target", default=None)

        assert "target=PosixPath('/srv')" in invoke(command(Where), ["--target", "/srv"]).output

    def test_the_command_keeps_its_class_and_callback_names(self):
        main = command(Picks)

        assert type(main).__module__ == "click.core" and type(main).__name__ == "Command"
        assert main.callback.__name__ == "main"

    def test_the_default_policy_adds_no_input(self):
        @options(Picks)
        @click.command()
        def main(settings):
            pass

        output = invoke(main, ["--help"]).output

        assert "  --no-input          Don't ask any questions - fail on a missing value instead.\n" in output

    def test_a_file_options_help_names_the_long_option(self):
        class Secret(Settings, option_file=True):
            password = Setting(str, "Password", default="", option=("-p", "--password"))

        output = invoke(command(Secret), ["--help"]).output

        assert "--password-file FILE  Read --password from FILE.\n" in output

    def test_a_prefix_reaches_the_selector_and_the_variables(self):
        policy = Recording()
        main = command(Picks, prefix="OTHER_", interactive=policy)

        assert "Depth" in invoke(main, ["--help"], env={"OTHER_MODE": " slow "}).output
        assert "Depth" not in invoke(main, ["--help"], env={"PICK_MODE": "slow"}).output
        invoke(main, [], env={"OTHER_NAME": "x"})
        assert policy.given == [{"name": Source.ENV}]

    def test_the_variables_a_policy_is_told_of(self, tmp_path):
        class Variables(Settings):
            fixed = Setting(str, "Fixed", default="", env=NoEnv(reason=REASON))
            clash = Setting(str, "Clash", default="")
            direct = Setting(str, "Direct", default="")
            filed = Setting(str, "Filed", default="")
            unfiled = Setting(str, "Unfiled", default="", env_file=False)

        (tmp_path / "value").write_text("v")
        policy = Recording()
        env = {
            "FIXED": "f",
            "CLASH": "a",
            "CLASH_FILE": "b",
            "DIRECT": "d",
            "FILED_FILE": str(tmp_path / "value"),
            "UNFILED_FILE": str(tmp_path / "value"),
        }

        invoke(command(Variables, interactive=policy), [], env=env)

        assert policy.given == [{"direct": Source.ENV, "filed": Source.ENV_FILE}]

    def test_current_values_and_a_subset_reach_the_resolution(self):
        class Service(Settings, prefix="SVC_"):
            port = Setting(int, "Port", default=1)
            host = Setting(str, "Host", default="h")

        main = command(Service, current=lambda: {"SVC_PORT": "9"}, only=["port"], interactive=Recording())
        output = invoke(main, [], env={"SVC_HOST": "x"}).output

        assert "Service(port=9, host='h')" in output
        assert "{'port': 'current', 'host': 'default'}" in output


class TestPeek:
    def test_last(self):
        assert _last(["--mode", "a", "--mode"], ["--mode"]) == "a"
        assert _last(["--mode=b=c"], ["--mode"]) == "b=c"
        assert _last(["x", "--mode", "a", "y"], ["--mode"]) == "a"
        assert _last(["--mode", "a", "--other", "--mode=b"], ["--mode"]) == "b"

    def test_an_unknown_choice_keeps_the_default_for_click_to_refuse(self):
        result = invoke(command(Picks), ["--mode", "medium"])

        assert result.exit_code == 2 and "'medium' is not one of 'fast', 'slow'" in result.output


class TestPolicies:
    @pytest.mark.parametrize(
        ("stdin", "stdout", "asks"), [(True, True, True), (False, True, False), (True, False, False)]
    )
    def test_a_terminal_is_both_ends(self, monkeypatch, stdin, stdout, asks):
        class End(io.StringIO):
            def __init__(self, tty):
                super().__init__()
                self.tty = tty

            def isatty(self):
                return self.tty

        monkeypatch.setattr("sys.stdin", End(stdin))
        monkeypatch.setattr("sys.stdout", End(stdout))

        assert TerminalUnlessNoInput()({}, False) is asks


class TestPrompts:
    def test_questionary_io_reaches_every_prompt(self, monkeypatch):
        calls = []

        def fake(kind, *answers):
            replies = list(answers)

            def prompt(message, **keywords):
                calls.append((kind, keywords.get("input")))
                return type("Asked", (), {"ask": lambda self: replies.pop(0)})()

            return prompt

        monkeypatch.setattr(questionary, "password", fake("password", "s", "s"))
        monkeypatch.setattr(questionary, "text", fake("text", "t", "a", "", "20"))
        monkeypatch.setattr(questionary, "confirm", fake("confirm", True, True))

        class Every(Settings, prompt=Prompt.QUESTIONARY, prefix="EV_"):
            token = Setting(str, "Token", secret=True, confirm=True)
            name = Setting(str, "Name")
            tags = Setting(str, "Tags", multiple=True, default=())
            cost = Setting(int, "Cost", default=1, soft_max=5)
            sure = Setting(bool, "Sure", default=True)

        with questionary_io(input="in"):
            result = invoke(command(Every))

        assert result.exit_code == 0, result.output
        assert all(given == "in" for _kind, given in calls) and len(calls) == 8

    def test_a_confirm_defaults_to_the_settings_default(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            questionary,
            "confirm",
            lambda message, **keywords: (
                seen.append(keywords["default"]) or type("Asked", (), {"ask": lambda self: True})()
            ),
        )

        class On(Settings, prompt=Prompt.QUESTIONARY):
            on = Setting(bool, "On", default=True)

        invoke(command(On))

        assert seen == [True]

    def test_each_item_is_asked_with_the_question(self, monkeypatch):
        seen = []
        replies = ["a", ""]
        monkeypatch.setattr(
            questionary,
            "text",
            lambda message, **keywords: (
                seen.append(message) or type("Asked", (), {"ask": lambda self: replies.pop(0)})()
            ),
        )

        class Tags(Settings, prompt=Prompt.QUESTIONARY):
            tags = Setting(str, "Tags", multiple=True, default=())

        assert "Tags(tags=('a',))" in invoke(command(Tags)).output
        assert seen == ["Tags", "Tags"]

    def test_a_required_casters_question_shows_no_default(self):
        class Titled(Settings, prompt=Prompt.CLICK):
            title = Setting(str.title, "Title")

        output = invoke(command(Titled), input="\nada\n").output

        assert output.startswith("Title: \nTitle: ada\n")

    def test_the_two_entries_message(self, monkeypatch):
        replies = ["a", "b", "c", "c"]
        monkeypatch.setattr(
            questionary,
            "password",
            lambda message, **keywords: type("Asked", (), {"ask": lambda self: replies.pop(0)})(),
        )

        class Seed(Settings, prompt=Prompt.QUESTIONARY):
            seed = Setting(str, "Seed", secret=True, confirm=True)

        assert invoke(command(Seed)).output.startswith("The two entries don't match - try again.\n")

    def test_default_text_is_given_the_setting(self):
        seen = []

        class Named(Settings):
            port = Setting(int, "Port", default=1)

        invoke(
            command(Named, help=HelpFormat(default_text=lambda setting, shown: seen.append(setting.name))), ["--help"]
        )

        assert set(seen) == {"port"}


class TestChecks:
    def test_whole_messages(self):
        @options(Picks)
        @click.command()
        @click.option("-v", "--verbose", is_flag=True)
        def main(verbose, settings):
            pass

        assert unreachable(main) == [
            "--verbose is a plain click option - declare it as a Setting, or pass plain_options="
        ]

        @options(Picks)
        @click.command()
        @click.option("--one")
        @click.option("--two")
        def both(one, two, settings):
            pass

        with pytest.raises(AssertionError) as raised:
            assert_reachable(both)
        assert str(raised.value) == (
            "Not every setting can be reached:\n"
            "--one is a plain click option - declare it as a Setting, or pass plain_options=\n"
            "--two is a plain click option - declare it as a Setting, or pass plain_options="
        )
        assert unreachable(main, plain_options={"verbose": "because"}) == [
            "plain option verbose is excused with 'because' - write an exemption such as NoQuestion(reason=...)"
        ]
        with pytest.raises(AssertionError) as raised:
            assert_reachable(main)
        assert str(raised.value) == (
            "Not every setting can be reached:\n"
            "--verbose is a plain click option - declare it as a Setting, or pass plain_options="
        )

    def test_where_a_problem_is_names_every_choice(self):
        class Empty(Settings):
            level = Setting(int, "", default=1)

        class Two(Settings):
            mode = Selector("Mode", {Mode.FAST: Empty, Mode.SLOW: Fast}, default=Mode.FAST)
            mode_settings = mode.settings
            kind = Selector("Kind", {"a": Slow}, default="a")
            kind_settings = kind.settings

        assert unreachable(Two) == ["[mode=fast, kind=a] mode_settings__level has no question text - give it help text"]

    def test_a_command_leaves_out_what_it_excludes(self):
        class Quiet(Settings):
            hidden = Setting(str, "", default="")
            shown = Setting(str, "Shown", default="")

        assert unreachable(Quiet) == ["hidden has no question text - give it help text"]
        assert unreachable(command(Quiet, exclude=["hidden"])) == []

    def test_a_block_needs_both_markers_in_order(self, tmp_path):
        docs = tmp_path / "settings.md"
        for text in [
            "<!-- /settings:settings -->",
            "<!-- settings:settings -->",
            "<!-- /settings:settings --><!-- settings:settings -->",
        ]:
            docs.write_text(text)
            with pytest.raises(AssertionError) as raised:
                assert_docs_current(docs, Picks)
            assert str(raised.value) == (
                f"{docs} has no <!-- settings:settings --> ... <!-- /settings:settings --> block to hold the "
                "settings table."
            )

    def test_an_out_of_date_tables_message(self, tmp_path):
        docs = tmp_path / "settings.md"
        docs.write_text("<!-- settings:settings -->\n<!-- /settings:settings -->")

        with pytest.raises(AssertionError) as raised:
            assert_docs_current(docs, Picks)
        assert str(raised.value) == (
            f"The settings table in {docs} is out of date - regenerate it with write_settings_table()."
        )

    def test_unused_answers_are_all_named(self):
        with pytest.raises(AssertionError) as raised:
            with answers({"b": 1, "a": 2}):
                pass
        assert str(raised.value) == "No question used the answers for a, b."

    def test_both_given_is_the_whole_line(self, tmp_path):
        class Secret(Settings, option_file=True):
            password = Setting(str, "Password", default="")

        result = invoke(command(Secret), ["--password", "a", "--password-file", str(tmp_path)])

        assert result.output.endswith("\nError: --password and --password-file can't both be given\n")


def test_a_plain_function_is_a_policy():
    calls = []

    def policy(given, no_input):
        calls.append((given, no_input))
        return False

    output = invoke(command(Picks, interactive=policy)).output

    assert calls == [({}, False)] and "Picks(" in output
    assert "--no-input" not in invoke(command(Picks, interactive=policy), ["--help"]).output


def test_a_settings_error_shows_the_usage_line():
    class Bounded(Settings):
        size = Setting(int, "Size", default=1, max=2)

    result = invoke(command(Bounded), ["--size", "3"])

    assert result.output == "Usage: main [OPTIONS]\nTry 'main --help' for help.\n\nError: --size must be at most 2\n"


def test_the_docs_block_is_found_across_lines_and_kept_around(tmp_path):
    from isik.cli.testing import write_settings_table

    docs = tmp_path / "settings.md"
    docs.write_text("a\n<!-- settings:settings -->\nold\nlines\n<!-- /settings:settings -->\nb\n")

    write_settings_table(docs, Picks)

    text = docs.read_text()
    assert text.startswith("a\n<!-- settings:settings -->\n| Option |")
    assert text.endswith("|\n<!-- /settings:settings -->\nb\n")
    assert_docs_current(docs, Picks)


def test_the_file_options_help_and_the_default_separator_can_be_worded():
    class Lock(Settings, option_file=True):
        lock_page = Setting(str, "lock page", default="p")

    help_format = HelpFormat(
        default_separator=" ",
        default_text=lambda setting, shown: f"(default: {shown})",
        option_file_help=lambda setting, option: f"read {option} from FILE ({setting.name})",
    )

    output = invoke(command(Lock, help=help_format), ["--help"]).output

    assert "--lock-page TEXT       lock page (default: p)\n" in output
    assert "--lock-page-file FILE  read --lock-page from FILE (lock_page)\n" in output


def test_a_group_keeps_its_commands_in_help_and_runs_them():
    @options(Picks, interactive=lambda given, no_input: False)
    @click.group(invoke_without_command=True)
    def links(settings):
        click.echo(f"group {settings.name}")

    @links.command("add")
    def add():
        click.echo("added")

    output = invoke(links, ["--help"]).output

    assert "\nOptions:\n" in output and output.index("Options:") < output.index("Commands:")
    assert "\nCommands:\n  add\n" in output
    assert invoke(links, ["--name", "x", "add"]).output == "group x\nadded\n"


@pytest.mark.parametrize("asks", [True, False])
def test_a_command_knows_whether_it_asked(asks):
    from isik.cli import is_interactive

    seen = []

    @options(Picks, interactive=lambda given, no_input: asks)
    @click.group(invoke_without_command=True)
    def main(settings):
        seen.append(("group", is_interactive()))

    @main.command("sub")
    def sub():
        seen.append(("sub", is_interactive()))

    with answers({"mode": "fast", "size": 1, "name": "n"}) if asks else contextlib.nullcontext():
        invoke(main, ["sub"], catch_exceptions=False)

    assert seen == [("group", asks), ("sub", asks)]
    # click's contexts are per thread, so a new one is outside every command whatever another test left behind.
    outside = []
    thread = threading.Thread(target=lambda: outside.append(is_interactive()))
    thread.start()
    thread.join()
    assert outside == [False]


def test_a_groups_commands_are_listed_with_its_context():
    seen = []

    class Lazy(click.Group):
        def list_commands(self, ctx):
            seen.append(ctx.info_name)
            return super().list_commands(ctx)

    @options(Picks, interactive=lambda given, no_input: False)
    @click.group(cls=Lazy)
    def links(settings):
        pass

    @links.command("add")
    def add():
        pass

    assert "\nCommands:\n  add\n" in invoke(links, ["--help"]).output
    assert seen == ["links"]
