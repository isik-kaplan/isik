import dataclasses
from enum import Enum
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isik.common.settings import (
    KEEP_CURRENT,
    LINES,
    UNSET,
    AskedAtUse,
    Constant,
    Excludes,
    Group,
    Message,
    NoEnv,
    NoOption,
    NoQuestion,
    OnlyWhen,
    Prompt,
    ReplacedByFlow,
    Requires,
    Selector,
    Setting,
    Settings,
    SettingsAborted,
    SettingsError,
    Source,
)
from isik.common.settings.conversion import convert, display, display_path, expand, hint, split
from isik.common.settings.resolution import Resolution, every_selection, walk


REASON = "the value is chosen once when the service is installed, not per run"


class Level(Enum):
    LOW = "low"
    HIGH = "high"


class Compression(Enum):
    GZIP = "gzip"
    ZSTD = "zstd"


def declare():
    """
    The classes these tests share - declared again for every test (see `_declared`), since mutmut
    credits code to the tests that ran it, and what runs at import is credited to whichever test runs first.
    """

    class Server(Settings, prefix="APP_"):
        port = Setting(int, "Port to listen on", default=8080, min=1, max=65535)
        host = Setting(str, "Host to bind", default="localhost")
        debug = Setting(bool, "Debug mode", default=False)
        level = Setting(Level, "How much to log", default=Level.LOW)
        data = Setting(Path, "Where data lives", default=lambda: Path("~/data"))
        tags = Setting(str, "Tags", multiple=True, default=())

    class GzipSettings(Settings):
        level = Setting(int, "gzip level", default=6, min=1, max=9)

    class ZstdSettings(Settings):
        level = Setting(int, "zstd level", default=3, min=1, max=22, soft_max=19)
        threads = Setting(int, "zstd threads", default=1)

    class Backup(Settings, prefix="BACKUP_", soft_override="force"):
        compression = Selector(
            "Compression", {Compression.ZSTD: ZstdSettings, Compression.GZIP: GzipSettings}, default=Compression.ZSTD
        )
        compression_settings = compression.settings
        encrypt = Setting(bool, "Encrypt?", default=False)
        password = Setting(str, "Password", secret=True, default="", only_when=lambda values: values.encrypt)
        hash_len = Constant(32, reason="the derivation always makes a 32-byte seed for an ed25519 key")
        force = Setting(bool, "Force", default=False, negatable=False)

    class Comment(Settings):
        comment = Setting(str, "Comment", default="")
        salt = Setting(int, "Salt size", default=8)

        def check(self):
            if "\n" in self.comment:
                raise self.error("comment", "must not contain newlines")
            if self.salt < 8:
                raise self.error("the KDF needs a salt of at least 8 bytes")

    return Server, GzipSettings, ZstdSettings, Backup, Comment


Server, GzipSettings, ZstdSettings, Backup, Comment = declare()


@pytest.fixture(autouse=True)
def _declared(monkeypatch):
    for declared in declare():
        monkeypatch.setitem(globals(), declared.__name__, declared)


class TestDeclaring:
    def test_a_settings_class_is_a_frozen_dataclass(self):
        server = Server()

        assert dataclasses.is_dataclass(server)
        with pytest.raises(dataclasses.FrozenInstanceError):
            server.port = 1

    def test_instances_compare_and_hash_by_value(self):
        assert Server(port=1) == Server(port=1)
        assert Server(port=1) != Server(port=2)
        assert hash(Server(port=1)) == hash(Server(port=1))

    def test_replace_keeps_the_rest(self):
        assert Server(port=1).replace(host="example.com") == Server(port=1, host="example.com")
        assert dataclasses.replace(Server(), port=2).port == 2

    def test_a_lazy_default_is_made_each_time(self):
        assert Server().data == Path("~/data")

    def test_a_setting_without_a_default_is_required_in_code(self):
        class Needs(Settings):
            token = Setting(str, "Token")

        with pytest.raises(TypeError):
            Needs()
        assert Needs(token="x").token == "x"

    def test_required_and_a_default_contradict(self):
        with pytest.raises(TypeError, match=r"^A required setting has no default"):
            Setting(str, "x", default="a", required=True)

    def test_a_secret_stays_out_of_repr(self):
        class Login(Settings):
            password = Setting(str, "Password", secret=True, default="hunter2")

        assert "hunter2" not in repr(Login())

    def test_a_method_name_cant_be_a_setting(self):
        with pytest.raises(TypeError, match=r"^Bad\.source is the name of a Settings method"):

            class Bad(Settings):
                source = Setting(str, "x", default="")

    def test_a_subclass_adds_to_its_bases_settings(self):
        class Extended(Server):
            workers = Setting(int, "Workers", default=2)

        assert list(Extended().as_dict()) == ["port", "host", "debug", "level", "data", "tags", "workers"]
        assert Extended.settings_options()["prefix"] == "APP_"

    def test_a_subclass_can_change_its_bases_keywords(self):
        class Other(Server, prefix="OTHER_"):
            pass

        assert Other.from_env({"OTHER_PORT": "1", "APP_PORT": "2"}).port == 1

    def test_a_multiple_settings_list_default_becomes_a_tuple(self):
        assert Setting(str, "x", multiple=True, default=["a"]).default == ("a",)

    @pytest.mark.parametrize(
        ("keywords", "message"),
        [
            ({"option": 3}, r"^option= takes the option's name"),
            ({"env": 3}, r"^env= takes the variable's full name"),
            ({"question": 3}, r"^question= takes the question's text"),
            ({"prompt": "click"}, r"^prompt= takes a Prompt"),
        ],
    )
    def test_wrong_keyword_types_are_refused(self, keywords, message):
        with pytest.raises(TypeError, match=message):
            Setting(str, "x", **keywords)

    @pytest.mark.parametrize(
        ("keywords", "message"),
        [
            ({"env": "nope"}, r"^A Settings class's env= takes NoEnv"),
            ({"prompt": "click"}, r"^A Settings class's prompt= takes a Prompt"),
            ({"messages": {"required": "x"}}, r"^messages= is keyed by Message"),
        ],
    )
    def test_wrong_class_keywords_are_refused(self, keywords, message):
        with pytest.raises(TypeError, match=message):
            type("Bad", (Settings,), {}, **keywords)

    def test_only_when_takes_a_function(self):
        with pytest.raises(TypeError, match=r"^OnlyWhen takes a function"):
            OnlyWhen("yes")

    def test_replaced_by_flow_takes_the_flow(self):
        with pytest.raises(TypeError, match=r"^ReplacedByFlow's flow must be the function that asks"):
            ReplacedByFlow(flow="ask", reason=REASON)

        def ask_for_locks():
            pass

        assert ReplacedByFlow(flow=ask_for_locks, reason=REASON).flow is ask_for_locks

    def test_an_annotation_on_the_setting_is_kept(self):
        class Annotated(Settings):
            port: int = Setting(int, "Port", default=1)

        assert Annotated().port == 1

    def test_a_setting_reprs_by_name(self):
        assert repr(Server.__dataclass_fields__["port"].metadata["isik.settings"]) == "<Setting port>"

    def test_the_annotation_follows_the_kind(self):
        assert Setting(int, "x").annotation() is int
        assert Setting(int, "x", multiple=True).annotation() == tuple[int, ...]


class TestFromEnv:
    def test_values_come_from_prefixed_variables(self):
        server = Server.from_env(
            {"APP_PORT": "9000", "APP_DEBUG": "yes", "APP_LEVEL": "high", "APP_TAGS": "a, b,,c", "APP_DATA": "~/x"}
        )

        assert (server.port, server.debug, server.level, server.tags) == (9000, True, Level.HIGH, ("a", "b", "c"))
        assert server.data == Path.home() / "x"
        assert server.source("port") is Source.ENV
        assert server.source("host") is Source.DEFAULT
        assert server.sources("tags") == (Source.ENV, Source.ENV, Source.ENV)

    def test_the_os_environment_is_the_default(self, monkeypatch):
        monkeypatch.setenv("APP_PORT", "7")

        assert Server.from_env().port == 7

    def test_a_prefix_replaces_the_classes(self):
        assert Server.from_env({"X_PORT": "7"}, prefix="X_").port == 7

    def test_a_file_variable_is_read(self, tmp_path):
        (tmp_path / "port").write_text("7\n")

        server = Server.from_env({"APP_PORT_FILE": str(tmp_path / "port")})

        assert server.port == 7
        assert server.source("port") is Source.ENV_FILE

    def test_both_the_variable_and_its_file_is_refused(self, tmp_path):
        with pytest.raises(SettingsError, match=r"^APP_PORT and APP_PORT_FILE are both set"):
            Server.from_env({"APP_PORT": "1", "APP_PORT_FILE": str(tmp_path)})

    def test_env_file_false_ignores_the_file_variable(self):
        class Plain(Settings):
            log = Setting(str, "Log", default="", env_file=False)

        assert Plain.from_env({"LOG": "x", "LOG_FILE": "y"}).log == "x"

    def test_a_bad_value_names_the_variable_and_shows_the_value(self):
        with pytest.raises(SettingsError, match=r"^APP_PORT: expected a whole number, got 'eighty'$"):
            Server.from_env({"APP_PORT": "eighty"})

    @pytest.mark.parametrize(
        ("variable", "text", "error"),
        [
            ("APP_DEBUG", "maybe", "expected yes/no, true/false, on/off or 1/0"),
            ("APP_LEVEL", "most", "expected one of low, high"),
        ],
    )
    def test_each_kind_says_what_it_expected(self, variable, text, error):
        with pytest.raises(SettingsError, match=rf"^{variable}: {error}, got '{text}'$"):
            Server.from_env({variable: text})

    def test_a_secrets_bad_value_is_never_shown(self):
        class Login(Settings):
            pin = Setting(int, "PIN", secret=True, default=0)

        with pytest.raises(SettingsError, match=r"^PIN is not valid$"):
            Login.from_env({"PIN": "12x4"})

    def test_show_values_can_be_turned_off_for_a_class_or_a_setting(self):
        class Quiet(Settings, show_values=False):
            port = Setting(int, "Port", default=1)

        class Loud(Settings, show_values=False):
            port = Setting(int, "Port", default=1, show_value=True)

        with pytest.raises(SettingsError, match=r"^PORT is not valid$"):
            Quiet.from_env({"PORT": "x"})
        with pytest.raises(SettingsError, match=r"^PORT: expected"):
            Loud.from_env({"PORT": "x"})

    def test_a_missing_required_value_names_the_option(self):
        class Needs(Settings, prefix="APP_"):
            token = Setting(str, "Token")

        with pytest.raises(SettingsError, match=r"^--token is required$"):
            Needs.from_env({})

    def test_a_required_value_cant_be_empty(self):
        class Needs(Settings, prefix="APP_"):
            token = Setting(str, "Token", required=True)

        with pytest.raises(SettingsError, match=r"^APP_TOKEN must not be empty$"):
            Needs.from_env({"APP_TOKEN": ""})

    def test_hard_limits_are_errors(self):
        with pytest.raises(SettingsError, match=r"^APP_PORT must be at most 65535$"):
            Server.from_env({"APP_PORT": "70000"})
        with pytest.raises(SettingsError, match=r"^APP_PORT must be at least 1$"):
            Server.from_env({"APP_PORT": "0"})

    def test_hard_limits_apply_to_each_item(self):
        class Ports(Settings):
            ports = Setting(int, "Ports", multiple=True, default=(), max=10)

        with pytest.raises(SettingsError, match=r"^PORTS must be at most 10$"):
            Ports.from_env({"PORTS": "1,11"})

    def test_lines_separate_values_that_hold_commas(self):
        class Locks(Settings):
            lock = Setting(str, "Lock", multiple=True, separator=LINES, default=())

        assert Locks.from_env({"LOCK": "alice:a,b\n\n bob \n"}).lock == ("alice:a,b", "bob")

    def test_a_custom_variable_name_is_used_whole(self):
        class Named(Settings, prefix="APP_"):
            token = Setting(str, "Token", default="", env="GITHUB_TOKEN")

        assert Named.from_env({"GITHUB_TOKEN": "x", "APP_TOKEN": "y"}).token == "x"

    def test_no_env_skips_the_variable(self):
        class Fixed(Settings):
            kdf = Setting(str, "KDF", default="scrypt", env=NoEnv(reason=REASON))
            seed = Setting(str, "Seed", default="")

        class AllFixed(Settings, env=NoEnv(reason=REASON)):
            kdf = Setting(str, "KDF", default="scrypt")
            seed = Setting(str, "Seed", default="", env="SEED")

        assert Fixed.from_env({"KDF": "argon2"}).kdf == "scrypt"
        assert AllFixed.from_env({"KDF": "argon2", "SEED": "s"}) == AllFixed(seed="s")

    def test_current_values_come_after_the_environment(self):
        server = Server.from_env({"APP_PORT": "1"}, current={"APP_PORT": "2", "APP_HOST": "example.com"})

        assert (server.port, server.host) == (1, "example.com")
        assert server.source("host") is Source.CURRENT

    def test_a_bad_current_value_names_the_variable(self):
        with pytest.raises(SettingsError, match=r"^APP_PORT: expected a whole number, got 'x'$"):
            Server.from_env({}, current={"APP_PORT": "x"})

    def test_unset_stays_unset(self):
        class Client(Settings):
            keep = Setting(int, "Keep", default=UNSET, shown="the server's")

        client = Client.from_env({})

        assert client.keep is UNSET
        assert client.as_dict() == {}

    def test_a_caster_or_a_param_type_converts(self):
        class Upper:
            name = "upper"

            def convert(self, value, param, ctx):
                if value == "bad":
                    raise ValueError("not that one")
                return value.upper()

        class Formats(Settings):
            name = Setting(str.title, "Name", default="")
            code = Setting(Upper(), "Code", default="")

        assert Formats.from_env({"NAME": "ada lovelace", "CODE": "ab"}) == Formats(name="Ada Lovelace", code="AB")
        with pytest.raises(SettingsError, match=r"^CODE: not that one, got 'bad'$"):
            Formats.from_env({"CODE": "bad"})

    def test_a_click_style_error_message_is_used(self):
        class Failure(Exception):
            def format_message(self):
                return "is not a port"

        def parse_port(text):
            raise Failure()

        class Ports(Settings):
            port = Setting(parse_port, "Port", default=None)

        with pytest.raises(SettingsError, match=r"^PORT: is not a port, got 'x'$"):
            Ports.from_env({"PORT": "x"})

    def test_floats(self):
        class Ratio(Settings):
            ratio = Setting(float, "Ratio", default=0.5)

        assert Ratio.from_env({"RATIO": "0.25"}).ratio == 0.25
        with pytest.raises(SettingsError, match=r"^RATIO: expected a number"):
            Ratio.from_env({"RATIO": "half"})

    def test_choices_map_labels_to_values(self):
        class Sessions(Settings):
            see = Setting(int, "Who sees sessions", choices={"none": 0, "own": 1, "all": 2}, default=1)
            mode = Setting(str, "Mode", choices=["fast", Level.HIGH], default="fast")

        assert Sessions.from_env({"SEE": "all", "MODE": "high"}) == Sessions(see=2, mode=Level.HIGH)


class TestSelectorsAndGroups:
    def test_a_selector_picks_its_group(self):
        backup = Backup.from_env({"BACKUP_COMPRESSION": "gzip", "BACKUP_LEVEL": "4"})

        assert backup.compression is Compression.GZIP
        assert backup.compression_settings == GzipSettings(level=4)
        assert backup.compression_settings.source("level") is Source.ENV

    def test_the_default_group_is_the_default_choices(self):
        assert Backup().compression_settings == ZstdSettings()

    def test_without_a_default_the_first_group_is(self):
        class First(Settings):
            kind = Selector("Kind", {"a": GzipSettings, "b": ZstdSettings})
            kind_settings = kind.settings

        assert First(kind="a").kind_settings == GzipSettings()
        assert [selection for selection in every_selection(First)] == [{("kind",): "a"}, {("kind",): "b"}]

    def test_a_selected_group_needs_its_selector(self):
        with pytest.raises(TypeError, match=r"^Orphan\.settings holds the settings of a selector"):

            class Orphan(Settings):
                settings_for = Backup.compression.settings if False else None
                settings = Selector("x", {"a": GzipSettings}).settings

    def test_a_group_nests_another_class_under_a_prefix(self):
        class Lock(Settings):
            expires = Setting(int, "Lock expiry, in seconds", default=60)

        class Client(Settings, prefix="APP_"):
            lock = Group(Lock, prefix="LOCK_")

        client = Client.from_env({"APP_LOCK_EXPIRES": "5"})

        assert client.lock == Lock(expires=5)
        assert client.as_dict() == {"expires": 5}
        assert [entry.label() for entry in walk(Client, {}) if entry.name == "expires"] == ["--lock-expires"]

    def test_a_group_takes_only_a_settings_class(self):
        with pytest.raises(TypeError, match=r"^Group takes a Settings class"):
            Group(dict)

    def test_constants_and_groups_flatten_into_as_dict(self):
        assert Backup().as_dict() == {
            "compression": Compression.ZSTD,
            "level": 3,
            "threads": 1,
            "encrypt": False,
            "password": "",
            "hash_len": 32,
            "force": False,
        }

    def test_a_constant_is_never_read(self):
        backup = Backup.from_env({"BACKUP_HASH_LEN": "16"})

        assert backup.hash_len == 32
        assert backup.source("hash_len") is Source.CONSTANT

    def test_a_soft_limit_needs_the_override(self):
        with pytest.raises(
            SettingsError,
            match=r"^BACKUP_LEVEL is 20, above the recommended safe limit of 19 - this may take a long time"
            r" - pass --force to override$",
        ):
            Backup.from_env({"BACKUP_LEVEL": "20"})
        assert Backup.from_env({"BACKUP_LEVEL": "20", "BACKUP_FORCE": "1"}).compression_settings.level == 20

    def test_a_soft_limit_without_any_override_is_an_error(self):
        with pytest.raises(SettingsError, match=r"^LEVEL is 20, above the recommended safe limit of 19"):
            ZstdSettings.from_env({"LEVEL": "20"})

    def test_a_soft_limit_on_one_item(self):
        class Many(Settings, soft_override="force"):
            sizes = Setting(int, "Sizes", multiple=True, default=(), soft_max=5, soft_override="force")
            force = Setting(bool, "Force", default=False)

        assert Many.from_env({"SIZES": "1,9,9", "FORCE": "on"}).sizes == (1, 9, 9)
        with pytest.raises(SettingsError, match=r"^SIZES is 9"):
            Many.from_env({"SIZES": "1,9"})

    def test_only_when_drops_the_setting_and_refuses_it_given(self):
        assert Backup.from_env({"BACKUP_ENCRYPT": "1", "BACKUP_PASSWORD": "p"}).password == "p"
        with pytest.raises(SettingsError, match=r"^BACKUP_PASSWORD can't be used here$"):
            Backup.from_env({"BACKUP_PASSWORD": "p"})

    def test_only_when_with_its_own_message_and_no_default(self):
        class Keys(Settings):
            authorize = Setting(bool, "Authorize", default=False)
            output = Setting(
                Path,
                "Output",
                only_when=OnlyWhen(
                    lambda values: not values.authorize, message="%(name)s can't be used with --authorize"
                ),
            )

        assert Keys.from_env({"AUTHORIZE": "1"}).output is UNSET
        with pytest.raises(SettingsError, match=r"^OUTPUT can't be used with --authorize$"):
            Keys.from_env({"AUTHORIZE": "1", "OUTPUT": "x"})

    def test_requires_and_excludes(self):
        class Register(Settings):
            label = Setting(str, "Label", default="")
            register = Setting(
                str, "Register", default="", requires=Requires("label", message="%(name)s needs a non-empty %(other)s")
            )
            port = Setting(int, "Port", default=None, requires="register")
            authorize = Setting(
                bool, "Authorize", default=False, excludes=["register", Excludes("label", message="no %(other)s")]
            )

        assert Register.from_env({"REGISTER": "a@b", "LABEL": "x"}).register == "a@b"
        with pytest.raises(SettingsError, match=r"^REGISTER needs a non-empty --label$"):
            Register.from_env({"REGISTER": "a@b"})
        with pytest.raises(SettingsError, match=r"^PORT needs --register$"):
            Register.from_env({"PORT": "22"})
        with pytest.raises(SettingsError, match=r"^AUTHORIZE can't be used with REGISTER$"):
            Register.from_env({"AUTHORIZE": "1", "REGISTER": "a@b", "LABEL": "x"})
        with pytest.raises(SettingsError, match=r"^no LABEL$"):
            Register.from_env({"AUTHORIZE": "1", "LABEL": "x"})

    def test_messages_can_be_worded_by_the_class(self):
        class Worded(Settings, messages={Message.REQUIRED: "%(name)s, please"}):
            token = Setting(str, "Token")

        class Inner(Settings, messages={Message.ABOVE_MAX: "%(name)s: too big"}):
            size = Setting(int, "Size", default=1, max=2)

        class Outer(Settings, messages={Message.REQUIRED: "outer %(name)s"}):
            inner = Group(Inner)
            name = Setting(str, "Name")

        with pytest.raises(SettingsError, match=r"^--token, please$"):
            Worded.from_env({})
        with pytest.raises(SettingsError, match=r"^SIZE: too big$"):
            Outer.from_env({"SIZE": "3", "NAME": "x"})
        with pytest.raises(SettingsError, match=r"^outer --name$"):
            Outer.from_env({})


class TestCheck:
    def test_check_runs_when_an_instance_is_made(self):
        with pytest.raises(SettingsError, match=r"^--comment must not contain newlines$"):
            Comment(comment="a\nb")

    def test_an_error_names_the_setting_the_way_it_was_given(self):
        with pytest.raises(SettingsError, match=r"^COMMENT must not contain newlines$"):
            Comment.from_env({"COMMENT": "a\nb"})

    def test_an_error_can_be_a_whole_message(self):
        with pytest.raises(SettingsError, match=r"^the KDF needs a salt of at least 8 bytes$"):
            Comment.from_env({"SALT": "4"})

    def test_a_value_passed_in_code_comes_from_code(self):
        assert Comment().source("comment") is Source.CODE


class FakeAsker:
    def __init__(self, *answers, confirm=True):
        self.answers = list(answers)
        self.told = []
        self.asked = []
        self.confirm = confirm
        self.soft = None

    def ask(self, entry, default):
        self.asked.append((entry.key, default))
        return self.answers.pop(0)

    def tell(self, message):
        self.told.append(message)

    def confirm_soft(self, header, lines, question):
        self.soft = (header, lines, question)
        return self.confirm


def resolve(cls, *answers, confirm=True, **keywords):
    asker = FakeAsker(*answers, confirm=confirm)
    return Resolution(cls, asker=asker, **keywords).resolve(), asker


class TestQuestions:
    def test_each_unset_setting_is_asked_in_order_with_its_default(self):
        server, asker = resolve(Server, 1, "h", True, Level.HIGH, Path("~/d"), ("a",))

        assert server == Server(port=1, host="h", debug=True, level=Level.HIGH, data=Path.home() / "d", tags=("a",))
        assert [key for key, _default in asker.asked] == ["port", "host", "debug", "level", "data", "tags"]
        assert asker.asked[0] == ("port", 8080)
        assert server.source("port") is Source.QUESTION

    def test_options_and_variables_come_first(self):
        server, asker = resolve(
            Server,
            "h",
            Level.LOW,
            Path("x"),
            (),
            options={"port": (2, Source.OPTION)},
            environ={"APP_DEBUG": "1"},
        )

        assert (server.port, server.debug) == (2, True)
        assert [key for key, _default in asker.asked] == ["host", "level", "data", "tags"]

    def test_a_hard_limit_asks_again_naming_the_option(self):
        server, asker = resolve(Server, 0, 70000, 80, "h", False, Level.LOW, Path("x"), ())

        assert server.port == 80
        assert asker.told == ["Error: --port must be at least 1", "Error: --port must be at most 65535"]

    def test_soft_limits_are_confirmed_once_at_the_end(self):
        backup, asker = resolve(Backup, Compression.ZSTD, 20, 1, False, False)

        assert backup.compression_settings.level == 20
        assert asker.soft == (
            "These may take a long time:",
            ["--level is 20, above the recommended safe limit of 19 - this may take a long time"],
            "Continue anyway?",
        )

    def test_declining_the_soft_limits_aborts(self):
        with pytest.raises(SettingsAborted):
            resolve(Backup, Compression.ZSTD, 20, 1, False, False, confirm=False)

    def test_a_given_value_over_a_soft_limit_still_needs_the_override(self):
        with pytest.raises(SettingsError, match=r"pass --force to override$"):
            resolve(
                Backup, Compression.ZSTD, 1, False, False, options={"compression_settings__level": (20, Source.OPTION)}
            )

    def test_only_when_and_ask_when_skip_questions(self):
        class Lock(Settings):
            lock = Setting(bool, "Lock it?", default=False)
            expires = Setting(int, "Expires", default=60, ask_when=lambda values: values.lock)

        assert resolve(Lock, False)[0] == Lock()
        assert resolve(Lock, True, 5)[0] == Lock(lock=True, expires=5)
        assert resolve(Backup, Compression.ZSTD, 3, 1, True, "pw", False)[0].password == "pw"

    def test_exempt_settings_arent_asked(self):
        class Exempt(Settings):
            verbose = Setting(bool, "Verbose", default=False, question=NoQuestion(reason=REASON))
            overwrite = Setting(bool, "Overwrite", default=False, question=AskedAtUse(reason=REASON))
            name = Setting(str, "Name", default="x", question="What should it be called?")

        exempt, asker = resolve(Exempt, "y")

        assert exempt == Exempt(name="y")
        assert asker.asked == [("name", "x")]

    def test_ask_order_puts_settings_first(self):
        class Ordered(Settings, ask_order=["second"]):
            first = Setting(str, "First", default="")
            second = Setting(str, "Second", default="")

        _ordered, asker = resolve(Ordered, "b", "a")

        assert [key for key, _default in asker.asked] == ["second", "first"]

    def test_the_current_value_is_the_questions_default(self):
        _server, asker = resolve(Server, 1, "h", False, Level.LOW, Path("x"), (), current={"APP_PORT": "9"})

        assert asker.asked[0] == ("port", 9)

    def test_on_empty(self):
        class Token(Settings):
            token = Setting(str, "Token", secret=True, default="", on_empty=KEEP_CURRENT)
            made = Setting(str, "Password", secret=True, default="", on_empty=lambda: "generated")
            fixed = Setting(str, "Fixed", default="", on_empty="fallback")

        token, _asker = resolve(Token, "", "", "", current={"TOKEN": "kept"})
        no_current, _asker = resolve(Token, "", "typed", "")

        assert token == Token(token="kept", made="generated", fixed="fallback")
        assert no_current == Token(token="", made="typed", fixed="fallback")

    def test_an_empty_answer_leaves_an_unset_setting_unset(self):
        class Client(Settings):
            keep = Setting(int, "Keep", default=UNSET)

        assert resolve(Client, "")[0].keep is UNSET

    def test_a_required_setting_is_asked_with_no_default(self):
        class Needs(Settings):
            token = Setting(str, "Token")

        needs, asker = resolve(Needs, "t")

        assert needs.token == "t"
        assert asker.asked == [("token", None)]

    def test_an_excluded_setting_takes_its_default(self):
        class Subset(Settings):
            token = Setting(str, "Token")
            name = Setting(str, "Name", default="n")

        subset, asker = resolve(Subset, included=lambda entry: entry.name != "name" and entry.name != "token")

        assert subset.name == "n"
        assert subset.token is UNSET
        assert asker.asked == []


class TestDataclassGroups:
    @dataclasses.dataclass(frozen=True)
    class Cloudflare:
        token: str = dataclasses.field(default="", metadata={"help": "API token", "secret": True})
        zone: str | None = None
        records: tuple[str, ...] = ()
        manage: bool = True
        internal: int = dataclasses.field(default=0, init=False)

    def test_a_plain_dataclass_comes_back_as_itself(self):
        class Dns(Settings, prefix="APP_"):
            dns = Group.from_dataclass(TestDataclassGroups.Cloudflare, prefix="DNS_")

        dns = Dns.from_env({"APP_DNS_TOKEN": "t", "APP_DNS_RECORDS": "a,b", "APP_DNS_MANAGE": "off"}).dns

        assert dns == self.Cloudflare(token="t", records=("a", "b"), manage=False)
        assert Dns().as_dict() == {"token": "", "zone": None, "records": (), "manage": True, "internal": 0}

    def test_describe_gives_each_fields_setting_keywords(self):
        group = Group.from_dataclass(self.Cloudflare, describe=lambda field: {"shown": f"the {field.name}"})

        assert group.settings_class.__dataclass_fields__["zone"].metadata["isik.settings"].shown == "the zone"

    def test_required_fields_and_factories(self):
        @dataclasses.dataclass
        class Needs:
            name: str
            tags: tuple[str, ...] = dataclasses.field(default_factory=tuple)

        group = Group.from_dataclass(Needs)

        assert group.settings_class.from_env({"NAME": "x"}) == group.settings_class(name="x")

    def test_only_a_dataclass(self):
        with pytest.raises(TypeError, match=r"^from_dataclass\(\) takes a dataclass"):
            Group.from_dataclass(dict)

    def test_selector_groups_can_be_plain_dataclasses(self):
        class Plugins(Settings):
            dns = Selector(
                "DNS provider",
                {"cloudflare": Group.from_dataclass(TestDataclassGroups.Cloudflare)},
                default="cloudflare",
            )
            dns_settings = dns.settings

        assert Plugins().dns_settings == self.Cloudflare()


class TestConversion:
    def test_split_and_convert(self):
        setting = Setting(int, "x", multiple=True)

        assert split(setting, " 1, ,2 ") == ["1", "2"]
        assert convert(setting, "1,2") == (1, 2)

    def test_expand_any_path(self):
        assert expand("~") == "~"
        assert expand(Path("~")) == Path.home()
        assert expand(UNSET) is UNSET
        assert expand((Path("~"), "~", None)) == (Path.home(), "~", None)

    def test_display_path_uses_the_tilde(self):
        assert display_path(Path.home() / ".ssh" / "id") == "~/.ssh/id"
        assert display_path("/etc/hosts") == "/etc/hosts"
        assert display_path(None) == "None"

    @pytest.mark.parametrize(
        ("setting", "value", "shown"),
        [
            (Setting(str, "x"), "", None),
            (Setting(str, "x"), UNSET, None),
            (Setting(str, "x"), None, None),
            (Setting(int, "x", multiple=True), (1, 2), "1, 2"),
            (Setting(Level, "x"), Level.HIGH, "high"),
            (Setting(int, "x", choices={"none": 0, "all": 2}), 2, "all"),
            (Setting(int, "x", choices={"none": 0}), 5, "5"),
            (Setting(bool, "x"), True, "yes"),
            (Setting(bool, "x", choices={"Generate": False, "Authorize": True}), False, "no"),
            (Setting(str, "x"), Compression.GZIP, "gzip"),
        ],
    )
    def test_display(self, setting, value, shown):
        assert display(setting, value) == shown

    @pytest.mark.parametrize(
        ("keywords", "shown"),
        [
            ({}, ""),
            ({"min": 1, "max": 64}, " (1-64)"),
            ({"min": 1}, " (>= 1)"),
            ({"max": 9}, " (<= 9)"),
            ({"soft_max": 8}, " (soft max 8)"),
            ({"min": 1, "max": 10, "soft_max": 5}, " (1-10, soft max 5)"),
        ],
    )
    def test_hint(self, keywords, shown):
        assert hint(Setting(int, "x", **keywords)) == shown


@given(
    option=st.none() | st.integers(1, 100),
    variable=st.none() | st.integers(1, 100),
    answer=st.integers(1, 100),
    asking=st.booleans(),
)
def test_precedence_is_option_then_variable_then_answer_then_default(option, variable, answer, asking):
    class One(Settings):
        size = Setting(int, "Size", default=7)

    options = {} if option is None else {"size": (option, Source.OPTION)}
    environ = {} if variable is None else {"SIZE": str(variable)}
    resolution = Resolution(One, options=options, environ=environ, asker=FakeAsker(answer) if asking else None)

    expected = next(value for value in (option, variable, answer if asking else None, 7) if value is not None)
    assert resolution.resolve().size == expected


def test_prompt_is_an_enum():
    assert Prompt("click") is Prompt.CLICK


def test_no_option_is_an_exemption():
    assert NoOption(reason=REASON).rule == "settings.option"


class TestLabels:
    def test_a_bad_file_variable_is_named_as_the_file(self, tmp_path):
        (tmp_path / "port").write_text("x")

        with pytest.raises(SettingsError, match=r"^APP_PORT_FILE: expected a whole number"):
            Server.from_env({"APP_PORT_FILE": str(tmp_path / "port")})

    def test_a_setting_with_no_option_is_named_by_its_variable(self):
        class Hidden(Settings, prefix="APP_"):
            token = Setting(str, "Token", option=NoOption(reason=REASON))
            seed = Setting(str, "Seed", option=NoOption(reason=REASON), env=NoEnv(reason=REASON))

        with pytest.raises(SettingsError, match=r"^APP_TOKEN is required$"):
            Hidden.from_env({})
        with pytest.raises(SettingsError, match=r"^seed is required$"):
            Hidden.from_env({"APP_TOKEN": "t"})


def test_a_groups_check_names_its_setting_with_the_groups_prefix():
    class Lock(Settings):
        expires = Setting(int, "Expiry", default=60)

        def check(self):
            if self.expires > 3600:
                raise self.error("expires", "must be at most an hour")

    class Client(Settings, prefix="APP_"):
        lock = Group(Lock, prefix="LOCK_")

    with pytest.raises(SettingsError, match=r"^APP_LOCK_EXPIRES must be at most an hour$"):
        Client.from_env({"APP_LOCK_EXPIRES": "7200"})
    with pytest.raises(SettingsError, match=r"^--expires must be at most an hour$"):
        Lock(expires=7200)
