"""Two settings never share an option or a variable - the class, or the command, refuses it when made."""

import dataclasses
import re

import click
import pytest
from click.testing import CliRunner

from isik.cli import no_option_given, options
from isik.common.settings import LINES, Group, NoEnv, Selector, Setting, Settings


REASON = "the value is chosen once when the service is installed, not per run"


def refused(*lines):
    return pytest.raises(TypeError, match="^" + re.escape("\n".join(lines)) + "$")


def test_a_setting_and_a_groups_setting_on_the_same_variable():
    class Lock(Settings):
        file = Setting(str, "Lock page", default="")

    with refused(
        "Client gives two things one name - rename one:",
        "lock_settings__file and lock would both be APP_LOCK_FILE",
    ):

        class Client(Settings, prefix="APP_"):
            lock = Setting(str, "Locks", multiple=True, separator=LINES, default=())
            lock_settings = Group(Lock, prefix="LOCK_", option_prefix="lock-settings-")


def test_a_setting_and_anothers_file_form():
    with refused(
        "Pages gives two things one name - rename one:",
        "page_file and page would both be PAGE_FILE",
    ):

        class Pages(Settings):
            page = Setting(str, "Page", default="", option="--page")
            page_file = Setting(str, "Page file", default="", option="--page-path")


def test_a_flag_and_a_setting_named_after_its_negation():
    with refused(
        "Flags gives two things one name - rename one:",
        "no_color and color would both be --no-color",
    ):

        class Flags(Settings):
            color = Setting(bool, "Color", default=True, env="COLOR", option=("-c", "--color"))
            no_color = Setting(str, "Other", default="", env="OTHER")


def test_an_option_file_form():
    with refused("Files gives two things one name - rename one:", "token_file and token would both be --token-file"):

        class Files(Settings, option_file=True):
            token = Setting(str, "Token", default="")
            token_file = Setting(str, "Token file", default="", env="PATH_OF_TOKEN", env_file=False)


def test_without_their_file_forms_names_dont_clash():
    class Plain(Settings):
        log = Setting(str, "Log", default="", env_file=False)
        log_file = Setting(str, "Log file", default="")

    assert Plain.from_env({"LOG": "a", "LOG_FILE": "b"}) == Plain(log="a", log_file="b")


def test_one_name_in_two_choices_is_fine():
    class Fast(Settings):
        level = Setting(int, "Level", default=1)

    class Slow(Settings):
        level = Setting(int, "Level", default=2)

    class Picks(Settings):
        mode = Selector("Mode", {"fast": Fast, "slow": Slow}, default="fast")
        mode_settings = mode.settings

    assert Picks().mode_settings == Fast()


class One(Settings, prefix="ONE_"):
    verbose = Setting(bool, "Verbose", default=False)
    input = Setting(str, "Input", default="", option="--no-input", env="OTHER_INPUT")


def test_a_command_refuses_its_own_names_too():
    with refused(
        "main gives two things one name - rename one:", "input would be --no-input, which the command already is"
    ):

        @options(One)
        @click.command()
        def main(settings):
            pass

    with refused(
        "main gives two things one name - rename one:",
        "verbose would be --verbose, which the command already is",
        "input would be --no-input, which the command already is",
    ):

        @options(One)
        @click.command()
        @click.option("--verbose", is_flag=True)
        def main(verbose, settings):  # noqa: F811
            pass


def test_a_commands_prefix_is_checked_with_its_variables():
    class Settled(Settings, prefix="A_"):
        name = Setting(str, "Name", default="", option="--name")
        input = Setting(str, "Input", default="", option="--input")

    with refused(
        "main gives two things one name - rename one:", "input would be B_NO_INPUT, which the command already is"
    ):

        class Clashing(Settled):
            input = Setting(str, "Input", default="", env="B_NO_INPUT", option="--input")

        @options(Clashing, prefix="B_")
        @click.command()
        def main(settings):
            pass

    @options(Settled, interactive=lambda given, no_input: False)
    @click.command()
    def fine(settings):
        pass

    assert CliRunner().invoke(fine, []).exit_code == 0


def test_a_plugins_own_error_reaches_the_caller_as_it_is():
    class PluginError(ValueError):
        pass

    @dataclasses.dataclass(frozen=True)
    class Plugin:
        zone: str = ""

        def __post_init__(self):
            if self.zone == "bad":
                raise PluginError("zone is bad")

    class App(Settings, prefix="APP_"):
        dns = Group.from_dataclass(Plugin, prefix="DNS_")

    with pytest.raises(PluginError, match="^zone is bad$") as raised:
        App.from_env({"APP_DNS_ZONE": "bad"})
    assert raised.value.__cause__ is None

    @options(App, interactive=no_option_given())
    @click.command()
    def main(settings):
        pass

    assert isinstance(CliRunner().invoke(main, ["--dns-zone", "bad"]).exception, PluginError)


def test_no_env_names_nothing():
    class Fixed(Settings):
        kdf = Setting(str, "KDF", default="", env=NoEnv(reason=REASON))
        other = Setting(str, "Other", default="", env="KDF", option="--other")

    assert Fixed.from_env({"KDF": "x"}).other == "x"


def test_a_clash_beside_a_selector_is_named_once():
    class Fast(Settings):
        level = Setting(int, "Level", default=1)

    with refused("Twice gives two things one name - rename one:", "two and one would both be --same"):

        class Twice(Settings):
            mode = Selector("Mode", {"fast": Fast, "slow": Fast}, default="fast")
            mode_settings = mode.settings
            one = Setting(str, "One", default="", option="--same")
            two = Setting(str, "Two", default="", option="--same", env="TWO_ELSEWHERE")


def test_help_is_taken():
    class Helpful(Settings):
        about = Setting(str, "About", default="", option="--help")

    with refused("main gives two things one name - rename one:", "about would be --help, which the command already is"):

        @options(Helpful)
        @click.command()
        def main(settings):
            pass


def test_a_variable_clashes_only_under_the_commands_own_prefix():
    class Skips(Settings, prefix="A_"):
        no_input = Setting(bool, "Skip", default=False, option="--skip")

    @options(Skips, interactive=no_option_given())  # adds no --no-input, so A_NO_INPUT is free
    @click.command()
    def fine(settings):
        pass

    with refused(
        "main gives two things one name - rename one:", "no_input would be B_NO_INPUT, which the command already is"
    ):

        @options(Skips, prefix="B_")
        @click.command()
        def main(settings):
            pass
