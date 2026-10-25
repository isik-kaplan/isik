import io
from enum import Enum
from pathlib import Path

import click
import pytest
import questionary
from click.testing import CliRunner

from isik.cli import HelpFormat, no_option_given, options, terminal_unless_no_input
from isik.cli.commands import CasterType, _last
from isik.cli.prompts import Asker
from isik.cli.testing import (
    answers,
    assert_docs_current,
    assert_reachable,
    questionary_io,
    settings_table,
    unreachable,
    write_settings_table,
)
from isik.common.settings import (
    KEEP_CURRENT,
    UNSET,
    AskedAtUse,
    Constant,
    Group,
    Message,
    NoEnv,
    NoOption,
    NoQuestion,
    Prompt,
    ReplacedByFlow,
    Selector,
    Setting,
    Settings,
    SettingsError,
    Source,
)
from isik.common.settings.resolution import Resolution


REASON = "the value is chosen once when the service is installed, not per run"


class Compression(Enum):
    GZIP = "gzip"
    ZSTD = "zstd"


def declare():
    """
    The classes these tests share - declared again for every test (see `_declared`), since mutmut
    credits code to the tests that ran it, and what runs at import is credited to whichever test runs first.
    """

    class GzipSettings(Settings):
        level = Setting(int, "gzip level", default=6, min=1, max=9)

    class ZstdSettings(Settings):
        level = Setting(int, "zstd level", default=3, min=1, max=22, soft_max=19)

    class Backup(Settings, prefix="BACKUP_", soft_override="force", prompt=Prompt.CLICK):
        compression = Selector(
            "Compression", {Compression.ZSTD: ZstdSettings, Compression.GZIP: GzipSettings}, default=Compression.ZSTD
        )
        compression_settings = compression.settings
        target = Setting(Path, "Where to write the backup", default=lambda: Path.home() / "backups")
        encrypt = Setting(bool, "Encrypt the backup?", default=False)
        password = Setting(
            str,
            "Encryption password",
            secret=True,
            confirm=True,
            default="",
            only_when=lambda values: values.encrypt,
            option_file=True,
        )
        exclude = Setting(str, "Patterns to skip", multiple=True, default=())
        keep = Setting(int, "Backups to keep", default=UNSET, shown="the server's")
        force = Setting(bool, "Allow slow settings", default=False, negatable=False)

    class Fixed(Settings, prefix="APP_"):
        hash_len = Constant(32, reason="the derivation always makes a 32-byte seed for an ed25519 key")
        lock = Group(GzipSettings, prefix="LOCK_")
        kdf = Setting(str, "KDF | backend", default="scrypt", env=NoEnv(reason=REASON))
        token = Setting(str, "Token", secret=True, default="")
        name = Setting(str, "Name")
        on = Setting(bool, "On", default=True)
        note = Setting(str, "Note", default="", question=NoQuestion(reason=REASON))
        shown = Setting(int, "Shown", default=UNSET)

    class Inner(Settings):
        size = Setting(int, "Size", default=1)

    class Documented(Settings, prefix="DOC_"):
        name = Setting(str, "Name\nof it | here")
        quiet = Setting(bool, "Quiet", default=False, option=["-q", "--quiet"])
        on = Setting(bool, "On", default=True, negatable=False)
        hidden = Setting(str, "Hidden", default="x", option=NoOption(reason=REASON))
        level = Setting(int, "Level", default=None, option=("-l", "--level"))
        lock = Group(Inner, prefix="LOCK_")

    return GzipSettings, ZstdSettings, Backup, Fixed, Inner, Documented


GzipSettings, ZstdSettings, Backup, Fixed, Inner, Documented = declare()


@pytest.fixture(autouse=True)
def _declared(monkeypatch):
    for declared in declare():
        monkeypatch.setitem(globals(), declared.__name__, declared)


def command(settings_class=None, **keywords):
    settings_class = settings_class or Backup
    keywords.setdefault("interactive", no_option_given())

    @options(settings_class, **keywords)
    @click.command()
    @click.argument("source", required=False)
    def main(source, settings):
        click.echo(repr(settings))
        click.echo(repr({name: settings.source(name).value for name in ("compression", "encrypt")}))

    return main


def run(main, args=(), **keywords):
    return CliRunner().invoke(main, list(args), catch_exceptions=False, **keywords)


class TestOptions:
    def test_help_lists_the_chosen_groups_options(self):
        output = run(command(), ["--help"]).output

        assert "--compression [zstd|gzip]  Compression  [default: zstd]" in output
        assert "zstd level (1-22, soft max 19)  [default: 3]" in output
        assert "--target PATH" in output and "[default: ~/backups]" in output
        assert "--encrypt / --no-encrypt" in output and "[default: no]" in output
        assert "--password-file FILE" in output and "Read --password from FILE." in output
        assert "[default: the server's]" in output
        assert "--force                    Allow slow settings\n" in output

    def test_the_selector_picks_the_options_help_shows(self):
        assert "gzip level (1-9)" in run(command(), ["--compression", "gzip", "--help"]).output
        assert "gzip level (1-9)" in run(command(), ["--compression=gzip", "--help"]).output
        assert "zstd level" in run(command(), ["--compression", "nope", "--help"]).output

    def test_the_selector_is_read_from_the_environment_too(self):
        assert "gzip level" in run(command(), ["--help"], env={"BACKUP_COMPRESSION": "gzip"}).output
        assert (
            "zstd level"
            in run(command(), ["--help"], env={"BACKUP_COMPRESSION_FILE": "x", "BACKUP_COMPRESSION": "gzip"}).output
        )

    def test_options_give_values_with_their_source(self):
        output = run(
            command(),
            [
                "src",
                "--compression",
                "gzip",
                "--level",
                "4",
                "--encrypt",
                "--password",
                "pw",
                "--exclude",
                "a",
                "--exclude",
                "b",
            ],
        ).output

        assert (
            "compression=<Compression.GZIP: 'gzip'>, compression_settings=declare.<locals>.GzipSettings(level=4)"
            in output
        )
        assert "encrypt=True" in output and "exclude=('a', 'b')" in output
        assert "{'compression': 'option', 'encrypt': 'option'}" in output

    def test_an_error_is_a_usage_error_naming_the_option(self):
        result = CliRunner().invoke(command(), ["src", "--level", "40"])

        assert result.exit_code == 2
        assert "Error: --level must be at most 22" in result.output

    def test_an_option_given_for_a_setting_that_doesnt_apply(self):
        assert "--password can't be used here" in CliRunner().invoke(command(), ["--password", "x"]).output

    def test_a_file_option_is_read(self, tmp_path):
        (tmp_path / "password").write_text("pw\n")

        result = run(command(), ["--encrypt", "--password-file", str(tmp_path / "password"), "--keep", "1"])

        assert "Backup(" in result.output

    def test_a_file_option_and_its_option_cant_both_be_given(self, tmp_path):
        result = CliRunner().invoke(command(), ["--encrypt", "--password", "a", "--password-file", str(tmp_path)])

        assert "--password and --password-file can't both be given" in result.output

    def test_an_unreadable_file_option(self, tmp_path):
        result = CliRunner().invoke(command(), ["--encrypt", "--password-file", str(tmp_path / "missing")])

        assert "--password-file names" in result.output

    def test_a_bad_file_options_value(self, tmp_path):
        class Port(Settings, option_file=True):
            port = Setting(int, "Port", default=1)

        (tmp_path / "port").write_text("x")
        result = CliRunner().invoke(command(Port), ["--port-file", str(tmp_path / "port")])

        assert "Error: --port-file: expected a whole number, got 'x'" in result.output

    def test_names_metavars_and_kinds(self):
        class Upper(click.ParamType):
            name = "upper"

            def convert(self, value, param, ctx):
                return value.upper()

        class Kinds(Settings):
            ratio = Setting(float, "Ratio", default=0.5)
            code = Setting(Upper(), "Code", default="")
            title = Setting(str.title, "Title", default="")
            register = Setting(str, "Register", default="", metavar="USER@HOST", option=("-r", "--register"))
            hidden = Setting(str, "Hidden", default="", option=NoOption(reason=REASON))
            quiet = Setting(bool, "Quiet", default=False, option=["-q", "--quiet"])
            lock = Setting(str, "Lock", multiple=True, bare="", default=())

        main = command(Kinds)
        output = run(main, ["--help"]).output

        assert "--ratio FLOAT" in output and "--code UPPER" in output and "--title TEXT" in output
        assert "-r, --register USER@HOST" in output and "--hidden" not in output
        assert "-q, --quiet / --no-quiet" in output
        result = run(main, ["--code", "ab", "--title", "ada", "--lock", "--lock", "alice", "-q"]).output
        assert "code='AB', title='Ada', register='', hidden='', quiet=True, lock=('', 'alice')" in result

    def test_options_go_above_the_command(self):
        with pytest.raises(TypeError, match=r"^@options\(\.\.\.\) goes above @click\.command\(\)"):
            options(Backup)(lambda: None)

    def test_options_take_a_settings_class(self):
        with pytest.raises(TypeError, match=r"^options\(\) takes a Settings class"):
            options(dict)

    def test_only_and_exclude_pick_a_subset(self):
        assert "--target" not in run(command(only=["compression", "compression_settings"]), ["--help"]).output
        excluded = run(command(exclude=["target"]), ["--help"]).output
        assert "--target" not in excluded and "--encrypt" in excluded

    def test_a_prefix_replaces_the_classes(self):
        output = run(
            command(prefix="OTHER_"),
            ["--level", "1"],
            env={"OTHER_ENCRYPT": "1", "OTHER_PASSWORD": "p", "OTHER_KEEP": "1"},
        ).output

        assert "encrypt=True" in output

    def test_an_epilog_for_the_chosen_groups(self):
        main = command(
            epilog=lambda selection: f"Options below are for --compression={selection['compression'].value}."
        )

        assert "Options below are for --compression=gzip." in run(main, ["--compression", "gzip", "--help"]).output

    def test_the_settings_keyword_name(self):
        class One(Settings):
            size = Setting(int, "Size", default=1)

        @options(One, name="config", interactive=no_option_given())
        @click.command()
        def main(config):
            click.echo(config.size)

        assert run(main, ["--size", "3"]).output == "3\n"

    def test_a_caster_type_says_whats_wrong(self):
        caster_type = CasterType(Setting(lambda text: int(text), "x"))

        assert caster_type.convert(5, None, None) == 5
        with pytest.raises(click.BadParameter):
            caster_type.convert("x", None, None)

    def test_last_skips_consumed_values(self):
        assert _last(["--kdf", "--kdf"], ["--kdf"]) == "--kdf"
        assert _last(["--kdf", "a", "--kdf=b"], ["--kdf"]) == "b"
        assert _last(["--kdf"], ["--kdf"]) is None


class TestHelpFormat:
    def test_the_default_on_its_own_line_and_a_capped_column(self):
        main = command(help=HelpFormat(default_on_own_line=True, first_column_max=20))

        output = run(main, ["--help"]).output

        assert "Compression\n" in output and "[default: zstd]" in output

    def test_default_text_formats_the_default(self):
        main = command(help=HelpFormat(default_text=lambda setting, shown: shown and f"(default: {shown})"))

        output = run(main, ["--help"]).output

        assert "(default: zstd)" in output and "[default:" not in output

    def test_a_command_with_nothing_to_show(self):
        class Empty(Settings):
            pass

        @options(Empty, help=HelpFormat(first_column_max=10), interactive=lambda given, no_input: False)
        @click.command(add_help_option=False)
        def main(settings):
            pass

        ctx = click.Context(main)
        formatter = ctx.make_formatter()
        main.format_options(ctx, formatter)
        assert formatter.getvalue() == ""


class TestInteractive:
    def test_questions_are_asked_when_no_option_is_given(self):
        answers_typed = "gzip\n12\n4\n\ny\nsecret\nsecret\nx\n\n7\ny\n"

        output = run(command(), input=answers_typed).output

        assert "Error: --level must be at most 9" in output
        assert "Where to write the backup [~/backups]:" in output
        assert "exclude=('x',), keep=7, force=True" in output
        assert "{'compression': 'question', 'encrypt': 'question'}" in output

    def test_soft_limits_are_confirmed_at_the_end(self):
        output = run(command(), input="zstd\n20\n\nn\n\n\nn\ny\n").output

        assert "These may take a long time:\n  --level is 20" in output and "Continue anyway? [y/N]: y" in output

    def test_declining_aborts(self):
        result = CliRunner().invoke(command(), input="zstd\n20\n\nn\n\n\nn\nn\n")

        assert result.exit_code == 1 and "Aborted!" in result.output

    def test_terminal_unless_no_input(self, monkeypatch):
        main = command(interactive=terminal_unless_no_input())
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

        assert "--no-input" in run(main, ["--help"]).output
        assert "Backup(" in run(main, ["--no-input"]).output
        assert "Backup(" in run(main, [], env={"BACKUP_NO_INPUT": "yes"}).output

    def test_terminal_policy(self, monkeypatch):
        policy = terminal_unless_no_input()
        monkeypatch.setattr("sys.stdin", io.StringIO())

        assert policy({}, False) is False
        assert policy({}, True) is False

    def test_no_option_given_ignores_what_it_names_and_variables(self):
        policy = no_option_given(ignoring=["authorize"])

        assert policy({"authorize": Source.OPTION, "seed": Source.ENV}, False) is True
        assert policy({"label": Source.OPTION_FILE}, False) is False

    def test_a_variable_doesnt_stop_the_questions(self):
        output = run(command(), env={"BACKUP_ENCRYPT": "1"}, input="zstd\n3\n\npw\npw\n\n\nn\n").output

        assert "encrypt=True" in output and "{'compression': 'question', 'encrypt': 'env'}" in output

    def test_click_prompts_for_each_kind(self):
        class Every(Settings, prompt=Prompt.CLICK, prefix="EVERY_"):
            name = Setting(str, "Name")
            mode = Setting(bool, "Mode", choices={"Generate": False, "Authorize": True}, default=False)
            ratio = Setting(float, "Ratio", default=0.5)
            title = Setting(str.title, "Title", default="")
            keep = Setting(int, "Keep", default=UNSET, shown="the server's")
            token = Setting(str, "Token", secret=True)
            path = Setting(Path, "Path")

        output = run(command(Every), input="ada\nAuthorize\n0.25\nlovelace\nx\n3\nt\n/tmp/p\n").output

        assert "Every(name='ada', mode=True, ratio=0.25, title='Lovelace', keep=3, path=PosixPath('/tmp/p'))" in output
        assert "Error: expected a whole number" in output
        assert "Keep [the server's]:" in output

    def test_a_typed_default_is_kept_on_an_empty_line(self):
        class Typed(Settings, prompt=Prompt.CLICK):
            title = Setting(str.title, "Title", default="Ada")
            items = Setting(int, "Items", multiple=True, default=(1,))
            more = Setting(int, "More", multiple=True)

        output = run(command(Typed), input="\n\nx\n2\n\n").output

        assert "Typed(title='Ada', items=(1,), more=(2,))" in output
        assert "Title [Ada]:" in output

    def test_before_ask_runs_before_the_question(self):
        notes = []

        class Noted(Settings):
            output = Setting(Path, "Output", default=Path("/tmp/key"), before_ask=lambda default: notes.append(default))

        run(command(Noted), input="\n")

        assert notes == [Path("/tmp/key")]

    def test_scripted_answers(self):
        with answers(
            {
                "compression": Compression.ZSTD,
                "level": 20,
                "target": Path("/tmp"),
                "encrypt": False,
                "exclude": (),
                "keep": 3,
                "force": False,
            }
        ) as script:
            output = run(command(interactive=lambda given, no_input: False)).output

        assert "keep=3" in output
        assert script.soft == ["--level is 20, above the recommended safe limit of 19 - this may take a long time"]

    def test_scripted_answers_fail_on_a_missing_a_refused_and_an_unused_answer(self):
        with pytest.raises(AssertionError, match=r"^Asked compression, which the test gave no answer for\.$"):
            with answers({}):
                run(command())
        with pytest.raises(AssertionError, match=r"^Asked compression_settings__level again"):
            with answers({"compression": Compression.ZSTD, "compression_settings__level": 99}):
                run(command())
        with pytest.raises(AssertionError, match=r"^No question used the answers for nope\.$"):
            with answers({"nope": 1}):
                pass

    def test_scripted_soft_limits_can_be_declined(self):
        scripted = {
            "compression": Compression.ZSTD,
            "level": 20,
            "target": Path("/tmp"),
            "encrypt": False,
            "exclude": (),
            "keep": 3,
            "force": False,
            "confirm_soft": False,
        }
        with answers(scripted):
            assert run(command()).exit_code == 1

    def test_a_secret_with_its_current_value(self):
        class Token(Settings):
            token = Setting(str, "Token", secret=True, default="", on_empty=KEEP_CURRENT)

        output = run(command(Token, current=lambda: {"TOKEN": "kept"}), input="\n").output

        assert "Token()" in output  # a secret stays out of the repr
        assert "Token:" in output


class FakePrompt:
    def __init__(self, answer):
        self.answer = answer

    def ask(self):
        return self.answer


class TestQuestionary:
    @pytest.fixture
    def asked(self, monkeypatch):
        replies = {}
        calls = []

        def fake(kind):
            def prompt(message, **keywords):
                calls.append((kind, message, keywords))
                return FakePrompt(replies[kind].pop(0))

            return prompt

        for kind in ("select", "confirm", "password", "text"):
            replies[kind] = []
            monkeypatch.setattr(questionary, kind, fake(kind))
        return replies, calls

    def test_each_kind(self, asked):
        replies, calls = asked

        class Every(Settings, prompt=Prompt.QUESTIONARY, prefix="EVERY_"):
            mode = Selector("Mode", {"fast": GzipSettings, "slow": ZstdSettings}, default="fast")
            mode_settings = mode.settings
            debug = Setting(bool, "Debug", default=False)
            token = Setting(str, "Token", secret=True, confirm=True)
            pin = Setting(str, "PIN", secret=True, default="")
            name = Setting(str, "Name")
            tags = Setting(str, "Tags", multiple=True, default=())
            keep = Setting(int, "Keep", default=UNSET, shown="the server's")
            path = Setting(Path, "Path", default=Path("/tmp/x"))

        replies["select"] = ["slow"]
        replies["confirm"] = [True]
        replies["password"] = ["a", "b", "t", "t", "p"]
        replies["text"] = ["30", "4", "", "ada", "x", "", "", "/tmp/x"]

        output = run(command(Every)).output

        assert (
            "mode_settings=declare.<locals>.ZstdSettings(level=4), debug=True, name='ada', tags=('x',), "
            "keep=<Sentinel:SETTING_UNSET>, path=PosixPath('/tmp/x')" in output
        )
        assert "Error: --level must be at most 22" in output
        assert "The two entries don't match - try again." in output
        assert calls[0] == ("select", "Mode", {"choices": ["fast", "slow"], "default": "fast"})

    def test_ctrl_c_aborts(self, asked):
        replies, _calls = asked
        replies["confirm"] = [None]

        class One(Settings, prompt=Prompt.QUESTIONARY):
            debug = Setting(bool, "Debug", default=False)

        assert run(command(One)).exit_code == 1

    def test_soft_limits_with_questionary(self, asked):
        replies, _calls = asked
        replies["text"] = ["20"]
        replies["confirm"] = [True]

        class Slow(Settings, prompt=Prompt.QUESTIONARY, soft_override="force"):
            level = Setting(int, "Level", default=3, soft_max=19)
            force = Setting(bool, "Force", default=False, question=NoQuestion(reason=REASON))

        assert "Slow(level=20" in run(command(Slow)).output

    def test_selects_default_to_questionary_and_io_is_passed(self, asked):
        replies, calls = asked
        replies["select"] = ["gzip"]
        replies["text"] = ["", "4"]

        class Auto(Settings):
            kind = Selector("Kind", {"zstd": ZstdSettings, "gzip": GzipSettings})
            kind_settings = kind.settings

        with questionary_io(input="in", output="out"):
            output = run(command(Auto), input="4\n").output

        assert "kind='gzip'" in output
        assert calls[0][2]["input"] == "in" and calls[0][2]["output"] == "out"


class TestReachable:
    def test_a_class_and_a_command_built_from_it(self):
        assert_reachable(Backup)
        assert_reachable(command())

    def test_a_plain_click_option_on_the_command(self):
        @options(Backup)
        @click.command()
        @click.option("--verbose", is_flag=True)
        def main(verbose, settings):
            pass

        with pytest.raises(AssertionError, match=r"--verbose is a plain click option"):
            assert_reachable(main)
        assert_reachable(main, plain_options={"verbose": NoQuestion(reason=REASON)})
        assert unreachable(main, plain_options={"verbose": "because"})[0].startswith("plain option verbose is excused")

    def test_missing_question_text_unless_its_not_asked(self):
        class Quiet(Settings):
            one = Setting(str, "", default="")
            two = Setting(str, "Two", default="")
            quiet = Setting(str, "", default="", question=AskedAtUse(reason=REASON))
            flow = Setting(str, "", default="", question=ReplacedByFlow(flow=print, reason=REASON))

        assert unreachable(Quiet) == ["one has no question text - give it help text"]

    def test_every_selection_is_checked(self):
        class Empty(Settings):
            level = Setting(int, "", default=1)

        class Picks(Settings):
            kind = Selector("Kind", {"a": GzipSettings, "b": Empty}, default="a")
            kind_settings = kind.settings

        assert unreachable(Picks) == ["[kind=b] kind_settings__level has no question text - give it help text"]

    def test_only_a_settings_class_or_a_command(self):
        with pytest.raises(TypeError, match=r"^Takes a Settings class or a command"):
            assert_reachable(dict)


class TestDocs:
    def test_the_table(self):
        table = settings_table(Fixed)

        assert "| `--kdf` |  | KDF \\| backend | scrypt | yes |" in table
        assert "| `--token` | `APP_TOKEN` | Token |  | yes |" in table
        assert "| `--name` | `APP_NAME` | Name | required | yes |" in table
        assert "| `--on` / `--no-on` | `APP_ON` | On | on | yes |" in table
        assert f"| `--note` | `APP_NOTE` | Note | empty | no - {REASON} |" in table
        assert "| `--shown` | `APP_SHOWN` | Shown | unset | yes |" in table
        assert "**lock**\n\n" in table and "`--lock-level` | `APP_LOCK_LEVEL`" in table

    def test_a_commands_table_and_selector_sections(self):
        table = settings_table(command(exclude=["keep"]))

        assert "--keep" not in table and "| `--force` |" in table
        assert "**compression = zstd**" in table and "**compression = gzip**" in table

    def test_a_command_with_an_excluded_group(self):
        assert "--lock-level" not in settings_table(command(Fixed, exclude=["lock"]))

    def test_written_and_checked(self, tmp_path):
        docs = tmp_path / "settings.md"
        docs.write_text("# Settings\n\n<!-- settings:settings -->\nold\n<!-- /settings:settings -->\n\nEnd.\n")

        with pytest.raises(AssertionError, match=r"is out of date - regenerate it with write_settings_table\(\)"):
            assert_docs_current(docs, Fixed)
        write_settings_table(docs, Fixed)
        assert_docs_current(docs, Fixed)
        assert docs.read_text().endswith("<!-- /settings:settings -->\n\nEnd.\n")

    def test_a_file_without_the_block(self, tmp_path):
        docs = tmp_path / "settings.md"
        docs.write_text("nothing")

        with pytest.raises(
            AssertionError, match=r"has no <!-- settings:server --> \.\.\. <!-- /settings:server --> block"
        ):
            assert_docs_current(docs, Fixed, name="server")


def test_messages_reach_a_command():
    class Worded(Settings, messages={Message.BOTH_GIVEN: "pick one of %(name)s and %(other)s"}, option_file=True):
        token = Setting(str, "Token", default="")

    result = CliRunner().invoke(command(Worded), ["--token", "a", "--token-file", "x"])

    assert "pick one of --token and --token-file" in result.output


class TestEdges:
    def test_variables_that_cant_be_read_dont_decide_the_mode(self):
        class Mixed(Settings, prefix="M_"):
            kdf = Setting(str, "KDF", default="a", env=NoEnv(reason=REASON))
            seed = Setting(str, "Seed", default="")

        result = CliRunner().invoke(command(Mixed), [], env={"M_SEED": "a", "M_SEED_FILE": "b"}, input="\n\n")

        assert "M_SEED and M_SEED_FILE are both set" in result.output

    def test_a_terminal_without_no_input_asks(self, monkeypatch):
        class One(Settings, prefix="ONE_"):
            size = Setting(int, "Size", default=1)

        monkeypatch.setattr("isik.cli.policies.sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("isik.cli.policies.sys.stdout.isatty", lambda: True, raising=False)
        policy = terminal_unless_no_input()

        assert policy({}, False) is True

    def test_the_default_on_its_own_line_without_a_column_cap(self):
        output = run(command(help=HelpFormat(default_on_own_line=True)), ["--help"]).output

        assert "Compression\n" in output

    def test_questionary_asks_again_after_a_bad_answer(self, monkeypatch):
        replies = ["x", "", "4"]
        monkeypatch.setattr(questionary, "text", lambda message, **keywords: FakePrompt(replies.pop(0)))

        class One(Settings, prompt=Prompt.QUESTIONARY, prefix="ONE_"):
            size = Setting(int, "Size")

        output = run(command(One)).output

        assert "Error: expected a whole number" in output and "One(size=4)" in output

    def test_a_table_section_with_no_settings_of_its_own(self):
        class Only(Settings, prefix="O_"):
            lock = Group(GzipSettings, prefix="LOCK_")
            note = Setting(str, "Note", default="", shown="none yet", option=NoOption(reason=REASON))

        table = settings_table(command(Only, exclude=["note"]))

        assert table.startswith("**lock**")
        assert "| `--note` |" not in settings_table(Only) and "none yet" in settings_table(Only)


def test_outside_a_terminal_nothing_is_asked():
    assert "Backup(" in run(command(interactive=terminal_unless_no_input())).output


def test_a_setting_without_a_variable_is_still_reachable():
    assert unreachable(Fixed) == []


def test_the_whole_table_of_a_command():
    @options(Documented, prefix="CMD_")
    @click.command()
    def main(settings):
        pass

    assert settings_table(main) == (
        "| Option | Environment variable | Description | Default | Asked |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| `--name` | `CMD_NAME` | Name of it \\| here | required | yes |\n"
        "| `-q`, `--quiet` / `--no-quiet` | `CMD_QUIET` | Quiet | off | yes |\n"
        "| `--on` | `CMD_ON` | On | on | yes |\n"
        "|  | `CMD_HIDDEN` | Hidden | x | yes |\n"
        "| `-l`, `--level` | `CMD_LEVEL` | Level |  | yes |\n"
        "\n"
        "**lock**\n"
        "\n"
        "| Option | Environment variable | Description | Default | Asked |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| `--lock-size` | `CMD_LOCK_SIZE` | Size | 1 | yes |\n"
    )


@pytest.mark.parametrize(
    ("width", "cap", "help_text"),
    [
        (
            60,
            20,
            "  --a-rather-long-option-name TEXT\n"
            "                                  xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
            "                                  y\n"
            "                                  [default: d]\n",
        ),
        (
            30,
            20,
            "  --a-rather-long-option-name TEXT\n"
            "                                  xxxxxxxxxx\n"
            "                                  xxxxxxxxxx\n"
            "                                  xxxxxxxxxx\n"
            "                                  xxxxxx y\n"
            "                                  [default: d]\n",
        ),
        (
            60,
            None,
            "  --a-rather-long-option-name TEXT\n"
            "                                  xxxxxxxxxxxxxxxxxxxxxxxx\n"
            "                                  xxxxxxxxxxxx y\n"
            "                                  [default: d]\n",
        ),
    ],
)
def test_a_default_on_its_own_line_wraps_to_the_column_it_leaves(width, cap, help_text):
    class Laid(Settings):
        a_rather_long_option_name = Setting(str, "x" * 36 + " y", default="d")
        empty = Setting(str, "", default="e")

    @options(Laid, help=HelpFormat(default_on_own_line=True, first_column_max=cap), interactive=no_option_given())
    @click.command(context_settings={"max_content_width": width})
    def main(settings):
        pass

    output = CliRunner().invoke(main, ["--help"], terminal_width=width).output

    assert "\nOptions:\n" + help_text + "  --empty TEXT                    [default: e]\n" in output


class Recording:
    def __init__(self, adds_no_input=True):
        self.adds_no_input = adds_no_input
        self.seen = []

    def __call__(self, given, no_input):
        self.seen.append(no_input)
        return False


@pytest.mark.parametrize(
    ("args", "env", "no_input"),
    [
        ([], {}, False),
        (["--no-input"], {}, True),
        ([], {"BACKUP_NO_INPUT": "yes"}, True),
        ([], {"NO_INPUT": "yes"}, False),
    ],
)
def test_no_input_comes_from_the_option_or_the_prefixed_variable(args, env, no_input):
    policy = Recording()

    run(command(interactive=policy), args, env=env)

    assert policy.seen == [no_input]


def test_a_bad_no_input_variable_is_a_usage_error():
    result = CliRunner().invoke(command(interactive=Recording()), [], env={"BACKUP_NO_INPUT": "maybe"})

    assert result.exit_code == 2
    assert "BACKUP_NO_INPUT: expected yes/no, true/false, on/off or 1/0, got 'maybe'" in result.output


def test_a_policy_that_adds_no_no_input_never_reads_the_variable():
    policy = Recording(adds_no_input=False)
    main = command(interactive=policy)

    run(main, [], env={"BACKUP_NO_INPUT": "yes"})

    assert policy.seen == [False]
    assert "--no-input" not in run(main, ["--help"]).output


def test_each_soft_limit_is_checked_against_its_own_override():
    class Two(Settings, prefix="TWO_"):
        cost = Setting(int, "Cost", default=1, soft_max=5, soft_override="force_cost")
        memory = Setting(int, "Memory", default=1, soft_max=5, soft_override="force_memory")
        force_cost = Setting(bool, "Force cost", default=False)
        force_memory = Setting(bool, "Force memory", default=False)

    result = CliRunner().invoke(command(Two), ["--cost", "9", "--memory", "9", "--force-cost"])

    assert "Error: --memory is 9, above the recommended safe limit of 5" in result.output
    assert "pass --force-memory to override" in result.output


def test_a_given_soft_limit_after_an_answered_one_is_still_checked():
    with answers({"cost": 9}):
        with pytest.raises(SettingsError, match=r"^MIX_MEMORY is 9"):

            class Mixed(Settings, prefix="MIX_"):
                cost = Setting(int, "Cost", default=1, soft_max=5)
                memory = Setting(int, "Memory", default=1, soft_max=5, question=NoQuestion(reason=REASON))

            Resolution(Mixed, environ={"MIX_MEMORY": "9"}, asker=Asker(Mixed, Resolution(Mixed).message)).resolve()


def test_both_given_names_the_long_option():
    class Short(Settings, prefix="S_", option_file=True):
        token = Setting(str, "Token", default="", option=("-t", "--token"))

    result = CliRunner().invoke(command(Short), ["-t", "a", "--token-file", "x"])

    assert "--token and --token-file can't both be given" in result.output
