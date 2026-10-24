"""The finer points of declaring and resolving settings - each one something a change could quietly break."""

import dataclasses
import typing
from enum import Enum
from pathlib import Path

import pytest

from isik.common.config import ConfigError, config, string
from isik.common.config.environment import read_variable
from isik.common.settings import (
    UNSET,
    Constant,
    Group,
    Message,
    NoEnv,
    NoOption,
    NoQuestion,
    Selector,
    Setting,
    Settings,
    SettingsError,
    Source,
)
from isik.common.settings.exemptions import IsConstant
from isik.common.settings.resolution import Resolution, every_selection, walk


REASON = "the value is chosen once when the service is installed, not per run"


class Mode(Enum):
    FAST = "fast"
    SLOW = "slow"


class TestDeclarationMessages:
    @pytest.mark.parametrize(
        ("keywords", "message"),
        [
            ({"option": 3}, "option= takes the option's name, or NoOption(reason=...), not 3."),
            ({"env": 3}, "env= takes the variable's full name, or NoEnv(reason=...), not 3."),
            (
                {"question": 3},
                "question= takes the question's text, or an exemption such as NoQuestion(reason=...), not 3.",
            ),
            ({"prompt": "click"}, "prompt= takes a Prompt, not 'click'."),
        ],
    )
    def test_the_whole_message(self, keywords, message):
        with pytest.raises(TypeError) as raised:
            Setting(str, "x", **keywords)
        assert str(raised.value) == message

    def test_a_group_after_its_selector_and_asked_after_it(self):
        class Fast(Settings):
            size = Setting(int, "Size", default=1)

        with pytest.raises(TypeError, match=r"^Early\.mode_settings comes before its selector mode - declare"):
            selector = Selector("Mode", {"fast": Fast}, default="fast")

            class Early(Settings):
                mode_settings = selector.settings
                mode = selector

        with pytest.raises(TypeError, match=r"^Asked\.mode_settings comes before its selector mode"):
            selector = Selector("Mode", {"fast": Fast}, default="fast")

            class Asked(Settings, ask_order=["mode_settings"]):
                mode = selector
                mode_settings = selector.settings


class TestDeclarations:
    def test_question_text_is_the_question_or_the_help(self):
        assert Setting(str, "Name", question="What's it called?").question_text() == "What's it called?"
        assert Setting(str, "Name", question=NoQuestion(reason=REASON)).question_text() == "Name"
        assert Setting(str, "Name").question_text() == "Name"

    def test_annotations(self):
        class Typed(Settings):
            port = Setting(int, "Port", default=1)
            tags = Setting(str, "Tags", multiple=True, default=())
            title = Setting(str.title, "Title", default="")
            mode = Selector("Mode", {}, default=None)
            fixed = Constant(1, reason=REASON)
            plain: int = 3

        hints = typing.get_type_hints(Typed)

        assert hints["port"] is int and hints["tags"] == tuple[str, ...] and hints["title"] is typing.Any
        assert hints["mode"] is str and "fixed" not in hints and hints["plain"] is int
        assert Typed(plain=4).plain == 4

    def test_a_selector_is_typed_by_its_keys_and_passes_its_keywords_on(self):
        class Fast(Settings):
            size = Setting(int, "Size", default=1)

        selector = Selector("Mode", {Mode.FAST: Fast, Mode.SLOW: Fast}, default=Mode.FAST, env=NoEnv(reason=REASON))

        assert selector.annotation() is Mode
        assert isinstance(selector.env, NoEnv)
        assert (selector.label(Mode.FAST), selector.label(Mode.SLOW)) == ("fast", "slow")

    def test_a_constant_keeps_its_reason(self):
        constant = Constant(32, reason=REASON)

        assert isinstance(constant.reason, IsConstant) and constant.reason.reason == REASON

    def test_a_constant_cant_be_passed(self):
        class Fixed(Settings):
            size = Constant(32, reason=REASON)

        with pytest.raises(TypeError):
            Fixed(size=16)

    def test_every_field_carries_its_declaration(self):
        class Sub(Settings):
            x = Setting(int, "X", default=1)

        class Every(Settings):
            needed = Setting(str, "Needed")
            made = Setting(str, "Made", default=lambda: "m", secret=True)
            plain = Setting(str, "Plain", default="p")
            fixed = Constant(1, reason=REASON)
            sub = Group(Sub)

        fields = {field.name: field for field in dataclasses.fields(Every)}

        assert all(field.metadata["isik.settings"].name == name for name, field in fields.items())
        assert "'m'" not in repr(Every(needed="n"))

    def test_a_group_can_name_its_options_prefix(self):
        class Lock(Settings):
            expires = Setting(int, "Expiry", default=1)

        class Client(Settings, prefix="APP_"):
            lock = Group(Lock, prefix="LOCK_", option_prefix="l-")

        found = [entry for entry in walk(Client, {}) if entry.name == "expires"]

        assert [(entry.label(), entry.env_name, entry.key) for entry in found] == [
            ("--l-expires", "APP_LOCK_EXPIRES", "lock__expires")
        ]

    def test_a_selectors_default_group_is_its_default_choices(self):
        class Fast(Settings):
            size = Setting(int, "Size", default=1)

        class Slow(Settings):
            size = Setting(int, "Size", default=2)

        class Picks(Settings):
            mode = Selector("Mode", {"fast": Fast, "slow": Slow}, default="slow")
            mode_settings = mode.settings

        assert Picks().mode_settings == Slow()


class TestDataclassGroups:
    def test_metadata_and_names(self):
        @dataclasses.dataclass
        class Plugin:
            token: str = dataclasses.field(default="", metadata={"help": "Token", "secret": True, "shown": "none"})
            internal: int = dataclasses.field(default=0, init=False)
            zone: str | None = None
            either: int | str | None = None
            ports: list[int] = dataclasses.field(default_factory=list)
            plain: str = "p"

        group = Group.from_dataclass(Plugin, option_prefix="p-")
        settings = {field.name: field.metadata["isik.settings"] for field in dataclasses.fields(group.settings_class)}

        assert (settings["token"].help, settings["token"].secret, settings["token"].shown) == ("Token", True, "none")
        assert settings["plain"].help == ""
        assert (settings["zone"].kind, settings["either"].kind, settings["ports"].kind) == (
            str,
            int | str | None,
            list[int],
        )
        assert "internal" not in settings
        assert group.settings_class.__module__ == Plugin.__module__
        assert group.option_prefix == "p-"
        assert group.settings_class.from_env({"PLAIN": "q", "ZONE": "z"}).plain == "q"

    def test_a_field_without_a_default_is_required(self):
        @dataclasses.dataclass
        class Needs:
            name: str

        with pytest.raises(SettingsError, match=r"^--name is required$"):
            Group.from_dataclass(Needs).settings_class.from_env({})


class TestResolutionDetails:
    def test_a_nested_selector_is_found_and_walked(self):
        class Fast(Settings):
            size = Setting(int, "Size", default=1)

        class Slow(Settings):
            depth = Setting(int, "Depth", default=2)

        class Inner(Settings):
            mode = Selector("Mode", {"fast": Fast, "slow": Slow}, default="fast")
            mode_settings = mode.settings

        class Outer(Settings, prefix="APP_"):
            inner = Group(Inner, prefix="IN_")

        assert every_selection(Outer) == [{("inner", "mode"): "fast"}, {("inner", "mode"): "slow"}]
        found = [entry for entry in walk(Outer, {("inner", "mode"): "slow"}) if entry.name == "depth"]
        assert [(entry.key, entry.env_name, entry.label()) for entry in found] == [
            ("inner__mode_settings__depth", "APP_IN_DEPTH", "--in-depth")
        ]
        assert Outer.from_env({"APP_IN_MODE": "slow", "APP_IN_DEPTH": "5"}).inner.mode_settings == Slow(depth=5)

    def test_list_defaults(self):
        class Lists(Settings):
            many = Setting(str, "Many", multiple=True, default=lambda: ["a"])
            one = Setting(str.split, "One", default=lambda: ["b"])

        lists = Lists.from_env({})

        assert lists.many == ("a",) and lists.one == ["b"]

    def test_a_setting_that_doesnt_apply_keeps_its_default(self):
        class Lock(Settings):
            lock = Setting(bool, "Lock", default=False)
            expires = Setting(int, "Expiry", default=60, only_when=lambda values: values.lock)

        assert Lock.from_env({}).expires == 60

    def test_limits_at_their_edges(self):
        class Bounded(Settings):
            size = Setting(int, "Size", default=None, min=1, max=10, soft_max=5)

        assert Bounded.from_env({}).size is None
        assert Bounded.from_env({"SIZE": "5"}).size == 5
        with pytest.raises(SettingsError, match=r"^SIZE is 10"):
            Bounded.from_env({"SIZE": "10"})

    def test_labels(self):
        class Lock(Settings):
            seed = Setting(str, "Seed", option=NoOption(reason=REASON), env=NoEnv(reason=REASON))

        class Named(Settings, prefix="APP_"):
            port = Setting(int, "Port", option=("-p", "--port"))
            lock = Group(Lock, prefix="LOCK_")

        with pytest.raises(SettingsError, match=r"^--port is required$"):
            Named.from_env({})
        with pytest.raises(SettingsError, match=r"^lock\.seed is required$"):
            Named.from_env({"APP_PORT": "1"})

    def test_a_code_made_instance_without_an_option_is_named_by_its_variable(self):
        class Hidden(Settings, prefix="APP_"):
            token = Setting(str, "Token", default="", option=NoOption(reason=REASON))

            def check(self):
                if self.token == "bad":
                    raise self.error("token", "is bad")

        with pytest.raises(SettingsError, match=r"^APP_TOKEN is bad$"):
            Hidden(token="bad")

    def test_options_of_a_multiple_choice_setting(self):
        class Modes(Settings):
            modes = Setting(Mode, "Modes", multiple=True, default=())

        resolved = Resolution(Modes, options={"modes": (("fast", "slow"), Source.OPTION)}).resolve()

        assert resolved.modes == (Mode.FAST, Mode.SLOW)

    def test_the_roots_wording_reaches_its_groups(self):
        class Inner(Settings):
            name = Setting(str, "Name")

        class Outer(Settings, messages={Message.REQUIRED: "outer %(name)s"}):
            inner = Group(Inner)

        with pytest.raises(SettingsError, match=r"^outer --name$"):
            Outer.from_env({})

    def test_as_dict_flattens_groups_of_groups_and_leaves_unset_out(self):
        class Deep(Settings):
            keep = Setting(int, "Keep", default=UNSET)
            depth = Setting(int, "Depth", default=1)

        class Middle(Settings):
            deep = Group(Deep)

        class Top(Settings):
            middle = Group(Middle)

        assert Top().as_dict() == {"depth": 1}


class TestConfigDetails:
    def test_every_clash_is_named_nested_ones_too(self):
        with pytest.raises(ConfigError) as raised:
            config(
                {"LOG": string(), "LOG_FILE": string(), "A": {"KEY": string(), "KEY_FILE": string()}},
                prefix="APP",
                environ={},
                files=True,
            )
        assert str(raised.value) == (
            "With files=True, APP__A__KEY_FILE, APP__LOG_FILE would be read as another setting's file - rename it."
        )

    def test_an_unreadable_file_variable_names_itself(self, tmp_path):
        with pytest.raises(ConfigError, match=r"^X_FILE names "):
            read_variable({"X_FILE": str(tmp_path / "missing")}, "X")

    def test_a_carriage_return_alone_at_the_start_stays(self, tmp_path):
        path = tmp_path / "value"
        path.write_bytes(b"\rabc\r\n")

        assert read_variable({"X_FILE": str(path)}, "X") == ("\rabc", True)


class TestRemaining:
    def test_a_lazy_default_shows_in_repr(self):
        class Lazy(Settings):
            data = Setting(Path, "Data", default=lambda: Path("/d"))

        assert repr(Lazy()).endswith("Lazy(data=PosixPath('/d'))")

    def test_a_selector_inside_a_chosen_group(self):
        class Leaf(Settings):
            depth = Setting(int, "Depth", default=1)

        class Other(Settings):
            width = Setting(int, "Width", default=2)

        class Branch(Settings):
            kind = Selector("Kind", {"leaf": Leaf, "other": Other}, default="leaf")
            kind_settings = kind.settings

        class Root(Settings):
            mode = Selector("Mode", {"branch": Branch}, default="branch")
            mode_settings = mode.settings

        selections = every_selection(Root)

        assert selections == [
            {("mode",): "branch", ("mode_settings", "kind"): "leaf"},
            {("mode",): "branch", ("mode_settings", "kind"): "other"},
        ]
        assert [entry.key for entry in walk(Root, selections[1])][-1] == "mode_settings__kind_settings__width"

    def test_a_dataclass_groups_variables_have_only_the_outer_prefix(self):
        @dataclasses.dataclass
        class Plugin:
            plain: str = "p"

        class Outer(Settings, prefix="APP_"):
            plugin = Group.from_dataclass(Plugin)

        assert Outer.from_env({"APP_PLAIN": "q"}).plugin == Plugin(plain="q")


class TestNestingAndPaths:
    def test_a_roots_no_env_covers_every_group_it_nests(self):
        class Pbkdf2(Settings):
            iterations = Setting(int, "Iterations", default=600_000)

        class Lock(Settings):
            expires = Setting(int, "Expiry", default=1)
            seen = Setting(int, "Seen", default=1, env="LOCK_SEEN")

        class Own(Settings, env=NoEnv(reason="this group's own reason, which its own class gives it")):
            depth = Setting(int, "Depth", default=1)

        class Generate(Settings, prefix="KEYGEN_", env=NoEnv(reason=REASON)):
            kdf = Selector("KDF", {"pbkdf2": Pbkdf2}, default="pbkdf2")
            kdf_settings = kdf.settings
            lock = Group(Lock, prefix="LOCK_")
            own = Group(Own)
            seed = Setting(str, "Seed", default="", env="KEYGEN_SEED")

        environ = {"KEYGEN_ITERATIONS": "7", "KEYGEN_LOCK_EXPIRES": "7", "LOCK_SEEN": "7", "KEYGEN_SEED": "s"}
        generated = Generate.from_env(environ)

        assert generated.kdf_settings.iterations == 600_000
        assert (generated.lock.expires, generated.lock.seen, generated.seed) == (1, 7, "s")
        reasons = {entry.key: entry.env_exemption for entry in walk(Generate, {("kdf",): "pbkdf2"})}
        assert reasons["kdf_settings__iterations"].reason == REASON
        assert reasons["own__depth"].reason.startswith("this group's own reason")

    def test_a_click_path_kind_is_expanded_too(self):
        import click

        class Keys(Settings):
            output = Setting(click.Path(dir_okay=False, path_type=Path), "Output", default=None)

        assert Keys.from_env({"OUTPUT": "~/k"}).output == Path.home() / "k"

    def test_a_groups_entry_has_no_names_of_its_own(self):
        class Lock(Settings):
            expires = Setting(int, "Expiry", default=1)

        class Client(Settings):
            lock = Group(Lock)
            hash_len = Constant(32, reason=REASON)

        found = {entry.key: (entry.env_name, entry.option_names) for entry in walk(Client, {})}

        assert found["lock"] == (None, None) and found["hash_len"] == (None, None)


class TestNoHome:
    @pytest.fixture
    def homeless(self, monkeypatch):
        def expanduser(self):
            if str(self).startswith("~"):
                raise RuntimeError("Could not determine home directory.")
            return self

        monkeypatch.setattr(Path, "expanduser", expanduser)

    class Server(Settings, prefix="APP_"):
        data_dir = Setting(Path, "Data directory", default=lambda: Path("~/.local/state/app"))
        extra = Setting(Path, "Extra directories", multiple=True, default=())

    def test_a_default_under_home_names_the_setting(self, homeless):
        with pytest.raises(SettingsError) as raised:
            self.Server.from_env({})
        assert str(raised.value) == "--data-dir: there's no home directory to expand ~ in - set it to a full path"

    def test_a_variable_is_named_as_given_and_a_full_path_is_fine(self, homeless):
        with pytest.raises(SettingsError, match=r"^APP_EXTRA: there's no home directory"):
            self.Server.from_env({"APP_DATA_DIR": "/srv/app", "APP_EXTRA": "/a,~/b"})
        assert self.Server.from_env({"APP_DATA_DIR": "/srv/app"}).data_dir == Path("/srv/app")

    def test_the_message_can_name_the_variable_whatever_the_source(self, homeless):
        class Worded(
            Settings,
            prefix="APP_",
            messages={Message.NO_HOME: "%(variable)s is required when there is no home directory"},
        ):
            data_dir = Setting(Path, "Data directory", default=lambda: Path("~/d"))
            hidden = Setting(Path, "Hidden", default=Path("~/h"), env=NoEnv(reason=REASON), option="--hidden")

        with pytest.raises(SettingsError, match=r"^APP_DATA_DIR is required when there is no home directory$"):
            Worded.from_env({})
        with pytest.raises(SettingsError, match=r"^--hidden is required when there is no home directory$"):
            Worded.from_env({"APP_DATA_DIR": "/d"})
