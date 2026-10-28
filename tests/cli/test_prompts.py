"""The exact calls each question makes - apps' tests replace click.prompt and questionary and check them."""

from pathlib import Path

import click
import pytest
import questionary

from isik.cli import prompts
from isik.cli.prompts import Asker, ClickQuestion, QuestionaryQuestion, style_of
from isik.common.settings import UNSET, Prompt, Setting, Settings
from isik.common.settings.resolution import Resolution, entry


class Recorder:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, *args, **keywords):
        self.calls.append((args, keywords))
        return self.replies.pop(0)


class Asked:
    def __init__(self, answer):
        self.answer = answer

    def ask(self):
        return self.answer


def entry_for(setting, owner=None):
    owner = owner or type("Owner", (Settings,), {"value": setting})
    return entry(owner, setting, (), "", "")


def question(kind, setting, default, *, owner=None, text=None):
    here = entry_for(setting, owner)
    asker = Asker(here.owner, Resolution(here.owner).message)
    return kind(asker, here, setting.question_text(), default, text)


@pytest.fixture
def prompt(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(click, "prompt", recorder)
    return recorder


@pytest.fixture
def confirm(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(click, "confirm", recorder)
    return recorder


class TestClick:
    def test_a_required_secret(self, prompt):
        prompt.replies = ["s"]

        assert question(ClickQuestion, Setting(str, "Seed", secret=True, confirm=True), None).ask() == "s"
        assert prompt.calls == [(("Seed",), {"hide_input": True, "confirmation_prompt": True})]

    def test_an_optional_secret(self, prompt):
        prompt.replies = [""]

        question(ClickQuestion, Setting(str, "Passphrase", secret=True, default=""), "").ask()

        assert prompt.calls == [
            (("Passphrase",), {"hide_input": True, "confirmation_prompt": False, "default": "", "show_default": False})
        ]

    def test_an_int(self, prompt):
        prompt.replies = [5]

        question(ClickQuestion, Setting(int, "Cost"), 3).ask()

        assert prompt.calls == [(("Cost",), {"default": 3, "type": int})]

    def test_text_shows_only_a_non_empty_default(self, prompt):
        prompt.replies = ["a", "b"]

        question(ClickQuestion, Setting(str, "Label", default=""), "").ask()
        question(ClickQuestion, Setting(str, "Label", default="x"), "x").ask()

        assert prompt.calls == [
            (("Label",), {"default": "", "show_default": False}),
            (("Label",), {"default": "x", "show_default": True}),
        ]

    def test_a_path_shows_its_default_with_a_tilde(self, prompt):
        prompt.replies = [Path("/x")]
        default = Path.home() / ".ssh" / "id"

        question(ClickQuestion, Setting(Path, "Output"), default).ask()

        (message,), keywords = prompt.calls[0]
        assert message == "Output [~/.ssh/id]"
        assert keywords["default"] == default and keywords["show_default"] is False
        assert isinstance(keywords["type"], click.Path)

    def test_a_path_without_a_default_and_a_click_path_type(self, prompt):
        prompt.replies = [Path("/x"), Path("/y")]
        kind = click.Path(dir_okay=False, path_type=Path)

        question(ClickQuestion, Setting(Path, "Output"), None).ask()
        question(ClickQuestion, Setting(kind, "Output"), None).ask()

        assert prompt.calls[0][0] == ("Output",)
        assert prompt.calls[1][1]["type"] is kind

    def test_a_select(self, prompt):
        prompt.replies = ["fast"]
        setting = Setting(str, "Mode", choices=["slow", "fast"], default="slow")

        assert question(ClickQuestion, setting, "slow").ask() == "fast"
        (message,), keywords = prompt.calls[0]
        assert message == "Mode"
        assert keywords["default"] == "slow" and keywords["type"].choices == ("slow", "fast")

    def test_a_select_with_no_default(self, prompt):
        prompt.replies = ["slow"]

        question(ClickQuestion, Setting(str, "Mode", choices=["slow"]), None).ask()

        assert prompt.calls[0][1]["default"] is None

    def test_a_confirm(self, confirm):
        confirm.replies = [True, False]

        question(ClickQuestion, Setting(bool, "Lock it?", default=True), True).ask()
        question(ClickQuestion, Setting(bool, "Lock it?"), None).ask()

        assert confirm.calls == [(("Lock it?",), {"default": True}), (("Lock it?",), {"default": False})]

    def test_items_end_on_an_empty_line(self, prompt):
        prompt.replies = ["a", "", ""]

        assert question(ClickQuestion, Setting(str, "Users", multiple=True), None).ask() == ("a",)
        assert question(ClickQuestion, Setting(str, "Users", multiple=True, default=("z",)), ("z",)).ask() == ("z",)
        assert prompt.calls[0] == (("Users",), {"default": "", "show_default": False})

    def test_an_unset_default_is_shown_by_its_text(self, prompt):
        prompt.replies = [""]

        assert question(ClickQuestion, Setting(int, "Keep", default=UNSET, shown="the server's"), UNSET).ask() == ""
        assert prompt.calls == [(("Keep [the server's]",), {"default": "", "show_default": False})]

    def test_a_caster_with_a_default(self, prompt):
        prompt.replies = [""]

        assert question(ClickQuestion, Setting(str.title, "Title", default="Ada"), "Ada").ask() == "Ada"
        assert prompt.calls == [(("Title [Ada]",), {"default": "", "show_default": False})]


class TestQuestionary:
    @pytest.fixture
    def text(self, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(questionary, "text", lambda *args, **keywords: Asked(recorder(*args, **keywords)))
        return recorder

    def test_text_is_prefilled_with_the_default(self, text):
        text.replies = ["~/k"]

        assert question(QuestionaryQuestion, Setting(Path, "Key"), Path.home() / "k").ask() == Path.home() / "k"
        assert text.calls == [(("Key",), {"default": "~/k"})]

    def test_a_required_answer_is_asked_again(self, text):
        text.replies = ["", "x"]

        assert question(QuestionaryQuestion, Setting(str, "Name"), None).ask() == "x"
        assert text.calls[0] == (("Name",), {"default": ""})

    def test_an_unset_default(self, text):
        text.replies = [""]

        assert question(QuestionaryQuestion, Setting(int, "Keep", default=UNSET), UNSET).ask() == ""

    def test_a_select_and_a_confirm(self, monkeypatch):
        selected, confirmed = Recorder("b"), Recorder(True)
        monkeypatch.setattr(questionary, "select", lambda *args, **keywords: Asked(selected(*args, **keywords)))
        monkeypatch.setattr(questionary, "confirm", lambda *args, **keywords: Asked(confirmed(*args, **keywords)))

        assert question(QuestionaryQuestion, Setting(str, "Pick", choices=["a", "b"]), None).ask() == "b"
        assert question(QuestionaryQuestion, Setting(bool, "Sure?"), None).ask() is True
        assert selected.calls == [(("Pick",), {"choices": ["a", "b"], "default": None})]
        assert confirmed.calls == [(("Sure?",), {"default": False})]

    def test_a_secret_asked_once(self, monkeypatch):
        password = Recorder("p")
        monkeypatch.setattr(questionary, "password", lambda *args, **keywords: Asked(password(*args, **keywords)))

        assert question(QuestionaryQuestion, Setting(str, "Token", secret=True), None).ask() == "p"
        assert password.calls == [(("Token",), {})]

    def test_a_secret_asked_twice(self, monkeypatch):
        password = Recorder("p", "p")
        monkeypatch.setattr(questionary, "password", lambda *args, **keywords: Asked(password(*args, **keywords)))

        question(QuestionaryQuestion, Setting(str, "Seed", secret=True, confirm=True), None).ask()

        assert password.calls[1] == (("Repeat for confirmation:",), {})


class TestAsker:
    def test_the_style_comes_from_the_setting_then_its_class_then_the_root(self):
        class Root(Settings, prompt=Prompt.QUESTIONARY):
            pass

        class Owner(Settings, prompt=Prompt.CLICK):
            pass

        class Plain(Settings):
            pass

        def style(setting, owner, root):
            return style_of(entry(owner, setting, (), "", ""), root)

        assert style(Setting(str, "x", prompt=Prompt.QUESTIONARY), Owner, Plain) is Prompt.QUESTIONARY
        assert style(Setting(str, "x"), Owner, Root) is Prompt.CLICK
        assert style(Setting(str, "x"), Plain, Root) is Prompt.QUESTIONARY
        assert style(Setting(str, "x", choices=["a"]), Plain, Plain) is Prompt.QUESTIONARY
        assert style(Setting(str, "x"), Plain, Plain) is Prompt.CLICK

    def test_a_function_asks_the_question_itself(self, prompt):
        asked = Recorder(3)
        setting = Setting(int, "Cost", min=1, max=4, prompt=asked)
        here = entry_for(setting)

        assert Asker(here.owner, Resolution(here.owner).message).ask(here, 2, None) == 3
        assert asked.calls == [(("Cost (1-4)", 2), {})]
        assert prompt.calls == []

    def test_before_ask_runs_before_the_function(self):
        order = []
        setting = Setting(
            str,
            "Seed",
            before_ask=lambda default: order.append(("before", default)),
            prompt=lambda message, default: order.append(("asked", message)) or "s",
        )
        here = entry_for(setting)

        Asker(here.owner, Resolution(here.owner).message).ask(here, None, None)

        assert order == [("before", None), ("asked", "Seed")]

    def test_a_function_is_asked_again_after_an_answer_past_a_hard_limit(self, capsys):
        asked = Recorder(9, 2)

        class Costs(Settings):
            cost = Setting(int, "Cost", max=4, prompt=asked)

        resolution = Resolution(Costs)
        resolution.asker = Asker(Costs, resolution.message)

        assert resolution.resolve().cost == 2
        assert len(asked.calls) == 2
        assert "at most 4" in capsys.readouterr().out

    def test_the_question_carries_its_limits(self, prompt):
        prompt.replies = [2]
        setting = Setting(int, "Cost", min=1, max=4)
        here = entry_for(setting)

        Asker(here.owner, Resolution(here.owner).message).ask(here, 1, None)

        assert prompt.calls[0][0] == ("Cost (1-4)",)

    def test_soft_limits_are_listed_then_confirmed(self, confirm, capsys):
        confirm.replies = [True]

        class Root(Settings):
            pass

        assert Asker(Root, None).confirm_soft("These:", ["one", "two"], "Go?") is True
        assert capsys.readouterr().out == "These:\n  one\n  two\n"
        assert confirm.calls == [(("Go?",), {})]

    def test_soft_limits_with_questionary(self, monkeypatch, capsys):
        confirmed = Recorder(False)
        monkeypatch.setattr(questionary, "confirm", lambda *args, **keywords: Asked(confirmed(*args, **keywords)))

        class Root(Settings, prompt=Prompt.QUESTIONARY):
            pass

        assert Asker(Root, None).confirm_soft("These:", ["one"], "Go?") is False
        assert confirmed.calls == [(("Go?",), {"default": False})]

    def test_io_is_passed_to_questionary(self, monkeypatch):
        confirmed = Recorder(True)
        monkeypatch.setattr(questionary, "confirm", lambda *args, **keywords: Asked(confirmed(*args, **keywords)))
        token = prompts.questionary_io.set({"input": "in"})
        try:
            question(QuestionaryQuestion, Setting(bool, "Sure?"), None).ask()
        finally:
            prompts.questionary_io.reset(token)

        assert confirmed.calls == [(("Sure?",), {"default": False, "input": "in"})]

    def test_ctrl_c_is_an_abort(self):
        with pytest.raises(click.Abort):
            prompts._answered(None)
        assert prompts._answered("") == ""


class TestEmptyAnswers:
    def test_a_required_list_is_asked_until_it_has_an_item(self, prompt):
        prompt.replies = ["", "a", ""]

        assert question(ClickQuestion, Setting(str, "Users", multiple=True), None).ask() == ("a",)

    def test_an_optional_list_without_a_default_is_empty(self, prompt):
        prompt.replies = [""]

        assert question(ClickQuestion, Setting(str, "Users", multiple=True, default=None), None).ask() == ()

    def test_none_as_the_default_is_kept(self, prompt):
        prompt.replies = [""]

        assert question(ClickQuestion, Setting(int, "Port", default=None), None).ask() is None
        assert prompt.calls == [(("Port",), {"default": "", "show_default": False})]

    def test_a_current_value_answers_a_required_question(self, prompt):
        prompt.replies = [""]

        assert question(ClickQuestion, Setting(str.title, "Title"), "Kept").ask() == "Kept"


def limit(text):
    return None if text == "any" else int(text)


class TestACurrentNone:
    """A current value a caster made into None - "any" for no limit - is shown by its text and kept on Enter."""

    def test_click_shows_its_text_and_keeps_it_for_a_required_question(self, prompt):
        prompt.replies = [""]

        assert question(ClickQuestion, Setting(limit, "Uses", required=True), None, text="any").ask() is None
        assert prompt.calls == [(("Uses [any]",), {"default": "", "show_default": False})]

    def test_questionary_types_its_text_in_and_keeps_it_for_a_required_question(self, monkeypatch):
        recorder = Recorder("any")
        monkeypatch.setattr(questionary, "text", lambda *args, **keywords: Asked(recorder(*args, **keywords)))

        assert question(QuestionaryQuestion, Setting(limit, "Uses", required=True), None, text="any").ask() is None
        assert recorder.calls == [(("Uses",), {"default": "any"})]

    def test_a_value_that_shows_is_shown_as_it_is_not_by_its_text(self, prompt):
        prompt.replies = [""]

        assert question(ClickQuestion, Setting(lambda t: int(t, 0), "Mask"), 16, text="0x10").ask() == 16
        assert prompt.calls == [(("Mask [16]",), {"default": "", "show_default": False})]

    def test_through_a_resolve(self, prompt):
        class Links(Settings, prefix="APP_"):
            uses = Setting(limit, "Uses", required=True)

        prompt.replies = [""]
        resolution = Resolution(Links, current={"APP_USES": "any"})
        resolution.asker = Asker(Links, resolution.message)

        assert resolution.resolve().uses is None
        assert prompt.calls == [(("Uses [any]",), {"default": "", "show_default": False})]

    def test_through_a_resolve_with_questionary(self, monkeypatch):
        class Links(Settings, prefix="APP_"):
            uses = Setting(limit, "Uses", required=True, prompt=Prompt.QUESTIONARY)

        recorder = Recorder("any")
        monkeypatch.setattr(questionary, "text", lambda *args, **keywords: Asked(recorder(*args, **keywords)))
        resolution = Resolution(Links, current={"APP_USES": "any"})
        resolution.asker = Asker(Links, resolution.message)

        assert resolution.resolve().uses is None
        assert recorder.calls == [(("Uses",), {"default": "any"})]


class TestUnsetDefaults:
    @pytest.fixture
    def text(self, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(questionary, "text", lambda *args, **keywords: Asked(recorder(*args, **keywords)))
        return recorder

    def test_questionary_shows_an_unset_default_in_the_question_not_the_box(self, text):
        text.replies = ["/in", ""]
        setting = Setting(str, "Lock page", default=UNSET, shown="the server's")

        assert question(QuestionaryQuestion, setting, UNSET).ask() == "/in"
        assert question(QuestionaryQuestion, setting, UNSET).ask() == ""
        assert text.calls[0] == (("Lock page [the server's]",), {"default": ""})

    def test_a_real_default_is_still_typed_in(self, text):
        text.replies = ["x"]

        question(QuestionaryQuestion, Setting(str, "Name", default="ada"), "ada").ask()

        assert text.calls == [(("Name",), {"default": "ada"})]

    def test_an_unset_flag_says_so_in_the_question(self, confirm, monkeypatch):
        confirm.replies = [False]
        confirmed = Recorder(True)
        monkeypatch.setattr(questionary, "confirm", lambda *args, **keywords: Asked(confirmed(*args, **keywords)))
        setting = Setting(bool, "Lock it?", default=UNSET, shown="the server's")

        question(ClickQuestion, setting, UNSET).ask()
        question(QuestionaryQuestion, setting, UNSET).ask()

        assert confirm.calls == [(("Lock it? [the server's]",), {"default": False})]
        assert confirmed.calls == [(("Lock it? [the server's]",), {"default": False})]


def test_a_real_default_with_shown_text_gets_no_unset_hint(confirm):
    confirm.replies = [True]

    question(ClickQuestion, Setting(bool, "Lock it?", default=True, shown="the server's"), True).ask()

    assert confirm.calls == [(("Lock it?",), {"default": True})]
