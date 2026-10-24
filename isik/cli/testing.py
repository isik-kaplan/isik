"""
For an app's tests: scripted answers, questionary's input and output, the check that every setting can be
reached, and the settings table its docs carry.
"""

import contextlib
import re
from pathlib import Path

import click

from isik._internal.translation import gettext as _
from isik.cli.prompts import questionary_io as _questionary_io
from isik.cli.prompts import scripted
from isik.common.settings.base import Settings
from isik.common.settings.conversion import hint
from isik.common.settings.declarations import REQUIRED, UNSET, Group, SelectedGroup, Setting
from isik.common.settings.exemptions import QUESTION_EXEMPTIONS
from isik.common.settings.resolution import (
    declarations,
    default_of,
    display_default,
    every_selection,
    nested,
    walk,
)
from isik.common.settings.resolution import entry as make_entry
from isik.common.utils.exemptions import Exemption


class Script:
    def __init__(self, answers):
        self.answers = dict(answers)
        self.used = set()
        self.soft = []

    def answer(self, entry):
        key = entry.key if entry.key in self.answers else entry.name
        if key not in self.answers:
            raise AssertionError(_("Asked %(key)s, which the test gave no answer for.") % {"key": entry.key})
        if key in self.used:
            raise AssertionError(_("Asked %(key)s again - the answer the test gave was refused.") % {"key": entry.key})
        self.used.add(key)
        return self.answers[key]

    def confirm_soft(self, lines):
        self.soft.extend(lines)
        return self.answers.get("confirm_soft", True)


@contextlib.contextmanager
def answers(scripted_answers):
    """
    Answers questions by setting name - a group's as `group__name` when two share a name - instead of
    prompting. A command under it always asks. It fails on a question it has no answer for, an answer the
    setting refuses, and an answer no question used. `"confirm_soft": False` declines the soft-limit check.

        with answers({"encrypt": True, "password": "pw"}) as script:
            CliRunner().invoke(main, ["./photos"])
        script.soft   # the soft-limit lines it was asked to confirm
    """
    script = Script(scripted_answers)
    token = scripted.set(script)
    try:
        yield script
    finally:
        scripted.reset(token)
    unused = sorted(set(script.answers) - script.used - {"confirm_soft"})
    if unused:
        raise AssertionError(_("No question used the answers for %(keys)s.") % {"keys": ", ".join(unused)})


@contextlib.contextmanager
def questionary_io(*, input=None, output=None):
    """Every questionary prompt reads `input` and writes `output` - prompt_toolkit's, e.g. `create_pipe_input()`."""
    token = _questionary_io.set({key: value for key, value in (("input", input), ("output", output)) if value})
    try:
        yield
    finally:
        _questionary_io.reset(token)


def _target(target):
    """(Settings class, binding or None) for a Settings class or a command `options()` was put on."""
    binding = getattr(target, "isik_binding", None)
    if binding is not None:
        return binding.settings_class, binding
    if isinstance(target, type) and issubclass(target, Settings):
        return target, None
    raise TypeError(_("Takes a Settings class or a command options() was put on, not %(value)r.") % {"value": target})


def _entries(cls, binding, selection):
    if binding is not None:
        return list(binding.entries(selection))
    return [entry for entry in walk(cls, selection) if isinstance(entry.declaration, Setting)]


def unreachable(target, *, plain_options=None):
    """
    What `assert_reachable()` refuses, one line each - empty when every setting, in every combination its
    selectors allow, has an option, an environment variable and a question, or an exemption saying why not.
    Two settings sharing a name never gets this far: the class, or the command, refuses it when it's made.
    """
    cls, binding = _target(target)
    problems = []
    plain = dict(plain_options or {})
    for name, exemption in plain.items():
        if not isinstance(exemption, Exemption):
            problems.append(
                _("plain option %(name)s is excused with %(value)r - write an exemption such as NoQuestion(reason=...)")
                % {"name": name, "value": exemption}
            )
    if binding is not None:
        for param in binding.base_params:
            # A command's own params: click adds --help when it lists them, never to these.
            if isinstance(param, click.Option) and param.name not in plain:
                problems.append(
                    _("%(option)s is a plain click option - declare it as a Setting, or pass plain_options=")
                    % {"option": max(param.opts, key=len)}
                )
    for selection in every_selection(cls):
        where = ", ".join(
            f"{path[-1]}={key.value if hasattr(key, 'value') else key}" for path, key in selection.items()
        )
        for entry in _entries(cls, binding, selection):
            setting = entry.declaration
            prefix = f"[{where}] " if where else ""
            if setting.asked and not setting.question_text().strip():
                problems.append(prefix + _("%(key)s has no question text - give it help text") % {"key": entry.key})
    return problems


def assert_reachable(target, *, plain_options=None):
    """
    Fails unless every setting of `target` - a Settings class, or a command `options()` was put on - can be
    given by option, by environment variable and by answer, in every combination of its selectors, or
    carries an exemption saying why not. On a command, a plain click option fails it too: one a person
    can pass but never be asked for. `plain_options={"verbose": NoQuestion(reason=...)}` excuses one.
    """
    problems = unreachable(target, plain_options=plain_options)
    if problems:
        raise AssertionError(_("Not every setting can be reached:\n%(problems)s") % {"problems": "\n".join(problems)})


def _cell(text):
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _default_cell(setting):
    if setting.shown is not None:
        return setting.shown
    if setting.secret:
        return ""
    if setting.default is REQUIRED:
        return _("required")
    value = default_of(setting)
    if value is UNSET:
        return _("unset")
    if value is False:
        return _("off")
    if value is True:
        return _("on")
    if value == "" or value == ():
        return _("empty")
    return display_default(setting) or ""


def _asked_cell(setting):
    if isinstance(setting.question, QUESTION_EXEMPTIONS):
        return _("no - %(reason)s") % {"reason": setting.question.reason}
    return _("yes")


def _table(entries):
    lines = [
        "| " + " | ".join([_("Option"), _("Environment variable"), _("Description"), _("Default"), _("Asked")]) + " |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        setting = entry.declaration
        option = ", ".join(f"`{name}`" for name in entry.option_names or ())
        if setting.is_bool() and setting.negatable and entry.option_names:
            # As help shows it: `-q, --quiet / --no-quiet`.
            option += f" / `--no-{max(entry.option_names, key=len)[2:]}`"
        variable = f"`{entry.env_name}`" if entry.env_name else ""
        cells = [
            option,
            variable,
            _cell(setting.help + hint(setting)),
            _cell(_default_cell(setting)),
            _asked_cell(setting),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def settings_table(target):
    """
    The Markdown table of `target`'s settings, for its docs - a table for the class, then one for each group
    and for each choice of each selector, headed by it. `assert_docs_current()` keeps the docs' copy current.
    """
    cls, binding = _target(target)
    prefix = cls.settings_options()["prefix"] if binding is None else binding.prefix
    return "\n\n".join(_sections(cls, binding, (), (prefix, "", None), None)) + "\n"


def _sections(cls, binding, path, place, heading):
    """`place` is (env prefix, option prefix, NoEnv) - where `cls`'s settings sit."""
    rows, later = [], []
    for declaration in declarations(cls).values():
        here = make_entry(cls, declaration, path, *place)
        if binding is not None and not binding.includes(here):
            continue
        if isinstance(declaration, Setting):
            rows.append(here)
        elif isinstance(declaration, Group):
            later.append((declaration.settings_class, here.path, nested(here, declaration), declaration.name))
        elif isinstance(declaration, SelectedGroup):
            selector = declaration.selector
            for key, group in selector.groups.items():
                title = f"{selector.name} = {selector.label(key)}"
                later.append((group.settings_class, here.path, nested(here, group), title))
    sections = []
    if rows:
        table = _table(rows)
        sections.append(f"**{heading}**\n\n{table}" if heading else table)
    # A command takes or leaves out a whole top-level setting, so a group it takes, it takes all of.
    for group_class, group_path, group_place, title in later:
        sections.extend(_sections(group_class, None, group_path, group_place, title))
    return sections


START = "<!-- settings:%(name)s -->"
END = "<!-- /settings:%(name)s -->"


def _block(path, name):
    """(up to the start marker, the table between, from the end marker on)."""
    text = Path(path).read_text()
    start, end = START % {"name": name}, END % {"name": name}
    found = re.search(re.escape(start) + "(.*?)" + re.escape(end), text, re.DOTALL)
    if found is None:
        raise AssertionError(
            _("%(path)s has no %(start)s ... %(end)s block to hold the settings table.")
            % {"path": path, "start": start, "end": end}
        )
    return text[: found.start(1)], found.group(1), text[found.end(1) :]


def write_settings_table(path, target, *, name="settings"):
    """Writes `settings_table(target)` into `path`, between `<!-- settings:NAME -->` and `<!-- /settings:NAME -->`."""
    before, _inside, after = _block(path, name)
    Path(path).write_text(f"{before}\n{settings_table(target)}{after}")


def assert_docs_current(path, target, *, name="settings"):
    """Fails when the settings table in `path` isn't what `settings_table(target)` makes now."""
    _before, inside, _after = _block(path, name)
    if inside != "\n" + settings_table(target):
        raise AssertionError(
            _("The settings table in %(path)s is out of date - regenerate it with write_settings_table().")
            % {"path": path}
        )
