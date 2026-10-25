"""
Asking one setting's question, in click's line prompts or questionary's arrow-key ones. click and
questionary are looked up when a question is asked, never bound at import, so a test can swap either.
"""

import contextvars
from pathlib import Path

import click
import questionary

from isik._internal.translation import gettext as _
from isik.common.settings.conversion import Invalid, display, display_path, hint, item_converter
from isik.common.settings.declarations import UNSET, Prompt
from isik.common.settings.messages import Message


# `{"input": ..., "output": ...}` handed to every questionary prompt - see isik.cli.testing.questionary_io().
questionary_io = contextvars.ContextVar("isik_questionary_io", default=None)
# The answers isik.cli.testing.answers() scripted, when a test is running under it.
scripted = contextvars.ContextVar("isik_scripted_answers", default=None)
# What convert() returns for text it has already said is wrong - None may be a value.
RETRY = object()


def _io():
    return questionary_io.get() or {}


def _answered(answer):
    if answer is None:  # questionary's answer to Ctrl-C
        raise click.Abort()
    return answer


def style_of(entry, root):
    setting = entry.declaration
    style = setting.prompt or entry.owner.settings_options()["prompt"] or root.settings_options()["prompt"]
    if style is not None:
        return style
    return Prompt.QUESTIONARY if setting.choices is not None else Prompt.CLICK


class Asker:
    """What isik.common.settings' resolution asks through when a command runs interactively."""

    def __init__(self, root, resolution_messages):
        self.root = root
        self.messages = resolution_messages

    def ask(self, entry, default):
        script = scripted.get()
        if script is not None:
            return script.answer(entry)
        setting = entry.declaration
        if setting.before_ask is not None:
            setting.before_ask(default)
        message = setting.question_text() + hint(setting)
        if style_of(entry, self.root) is Prompt.QUESTIONARY:
            return QuestionaryQuestion(self, entry, message, default).ask()
        return ClickQuestion(self, entry, message, default).ask()

    def tell(self, message):
        click.echo(message)

    def confirm_soft(self, header, lines, question):
        script = scripted.get()
        if script is not None:
            return script.confirm_soft(lines)
        click.echo(header)
        for line in lines:
            click.echo(f"  {line}")
        if self.root.settings_options()["prompt"] is Prompt.QUESTIONARY:
            return _answered(questionary.confirm(question, default=False, **_io()).ask())
        return click.confirm(question)


class Question:
    def __init__(self, asker, entry, message, default):
        self.asker = asker
        self.entry = entry
        self.setting = entry.declaration
        self.message = message
        self.default = default

    def ask(self):
        setting = self.setting
        if setting.choices is not None:
            return self.select()
        if setting.is_bool():
            return self.confirm()
        if setting.secret:
            return self.secret()
        if setting.multiple:
            return self.items()
        return self.text()

    def convert(self, text):
        """The value of `text`, or RETRY after saying what's wrong with it."""
        try:
            return item_converter(self.setting)(text)
        except Invalid as error:
            self.asker.tell(self.asker.messages(self.entry.owner, Message.ASKED_ERROR, message=str(error)))
            return RETRY

    def unset_message(self):
        """The question, with `shown` after it for an unset default - a hint to read, never text to edit."""
        if self.default is UNSET and self.setting.shown:
            return f"{self.message} [{self.setting.shown}]"
        return self.message

    def empty(self):
        """What an empty answer gives - RETRY when there's nothing to fall back to and the setting is required."""
        if self.default is None and self.setting.required:
            return RETRY
        return "" if self.default is UNSET else self.default

    def items(self):
        found = []
        while True:
            text = self.line(self.message)
            if text == "" and found:
                return tuple(found)
            if text == "":
                value = self.empty()
                if value is not RETRY:
                    return () if value is None else value
                continue
            value = self.convert(text)
            if value is not RETRY:
                found.append(value)

    def default_label(self):
        return {value: label for label, value in self.setting.choices.items()}.get(self.default)


class ClickQuestion(Question):
    """The prompts click.prompt and click.confirm give - and the arguments they've always been called with."""

    def select(self):
        labels = self.setting.choices
        default = self.default_label()
        answer = click.prompt(self.message, type=click.Choice(list(labels)), default=default)
        return labels[answer]

    def confirm(self):
        return click.confirm(self.unset_message(), default=bool(self.default))

    def secret(self):
        if self.setting.required:
            return click.prompt(self.message, hide_input=True, confirmation_prompt=self.setting.confirm)
        return click.prompt(
            self.message, hide_input=True, confirmation_prompt=self.setting.confirm, default="", show_default=False
        )

    def line(self, message):
        return click.prompt(message, default="", show_default=False)

    def text(self):
        setting, default = self.setting, self.default
        if default is UNSET or (default is None and not setting.required):
            return self.converted_line()
        if setting.kind is int or setting.kind is float:
            return click.prompt(self.message, default=default, type=setting.kind)
        if setting.kind is Path or isinstance(setting.kind, click.Path):
            message = self.message if default is None else f"{self.message} [{display_path(default)}]"
            kind = setting.kind if isinstance(setting.kind, click.Path) else click.Path(path_type=Path)
            return click.prompt(message, default=default, type=kind, show_default=False)
        if setting.kind is str:
            return click.prompt(self.message, default=default, show_default=bool(default))
        return self.converted_line()

    def converted_line(self):
        shown = display(self.setting, self.default)  # None for an unset default, whose hint unset_message() gives
        message = f"{self.message} [{shown}]" if shown else self.unset_message()
        while True:
            text = click.prompt(message, default="", show_default=False)
            value = self.empty() if text == "" else self.convert(text)
            if value is not RETRY:
                return value


class QuestionaryQuestion(Question):
    def select(self):
        labels = self.setting.choices
        answer = questionary.select(self.message, choices=list(labels), default=self.default_label(), **_io()).ask()
        return labels[_answered(answer)]

    def confirm(self):
        return _answered(questionary.confirm(self.unset_message(), default=bool(self.default), **_io()).ask())

    def secret(self):
        while True:
            answer = _answered(questionary.password(self.message, **_io()).ask())
            if not self.setting.confirm:
                return answer
            again = _answered(questionary.password(_("Repeat for confirmation:"), **_io()).ask())
            if again == answer:
                return answer
            self.asker.tell(self.asker.messages(self.entry.owner, Message.NOT_CONFIRMED))

    def line(self, message):
        return _answered(questionary.text(message, **_io()).ask())

    def text(self):
        # Only a real default is typed in, ready to edit; an unset one's `shown` goes in the question.
        filled = display(self.setting, self.default)  # None for an unset one
        while True:
            text = _answered(questionary.text(self.unset_message(), default=filled or "", **_io()).ask())
            value = self.empty() if text == "" or (filled and text == filled) else self.convert(text)
            if value is not RETRY:
                return value
