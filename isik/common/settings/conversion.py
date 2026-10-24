"""
Turning text - an environment variable, a file, an answer - into a setting's value, and a value back into
the text help and docs show for it. One converter per setting, whichever way the text arrived.
"""

from enum import Enum
from pathlib import Path

from isik._internal.translation import gettext as _
from isik.common.config.casters import natural_boolean
from isik.common.settings.declarations import LINES, UNSET


class Invalid(ValueError):
    """Text a setting can't take - its message says what was expected, without the text."""


def _number(kind, expected):
    def convert(text):
        try:
            return kind(text.strip())
        except ValueError:
            raise Invalid(expected) from None

    return convert


def _boolean(text):
    try:
        return natural_boolean()(text)
    except ValueError:
        raise Invalid(_("expected yes/no, true/false, on/off or 1/0")) from None


def _picked(choices):
    def convert(text):
        try:
            return choices[text.strip()]
        except KeyError:
            raise Invalid(_("expected one of %(choices)s") % {"choices": ", ".join(choices)}) from None

    return convert


def _with(caster):
    def convert(text):
        try:
            if hasattr(caster, "convert"):  # a click ParamType
                return caster.convert(text, None, None)
            return caster(text)
        except Exception as error:
            detail = error.format_message() if hasattr(error, "format_message") else str(error)
            raise Invalid(detail) from None

    return convert


def item_converter(setting):
    """`text -> value` for one value of `setting`, raising `Invalid`."""
    if setting.choices is not None:
        return _picked(setting.choices)
    kind = setting.kind
    if kind is str:
        return str
    if kind is int:
        return _number(int, _("expected a whole number"))
    if kind is float:
        return _number(float, _("expected a number"))
    if kind is bool:
        return _boolean
    if kind is Path:
        return lambda text: Path(text)
    return _with(kind)


def split(setting, text):
    """The items of a multiple setting's text - stripped, with empty ones dropped."""
    parts = text.splitlines() if setting.separator is LINES else text.split(setting.separator)
    return [part.strip() for part in parts if part.strip()]


def convert(setting, text):
    one = item_converter(setting)
    if setting.multiple:
        return tuple(one(part) for part in split(setting, text))
    return one(text)


def expand(value):
    """`~` expanded in a path - whatever kind made it, `Path` or `click.Path(path_type=Path)` - and in each of many."""
    if isinstance(value, Path):
        return value.expanduser()
    if isinstance(value, tuple):
        return tuple(expand(item) for item in value)
    return value


def display_path(path):
    """A path as a person would write it - `~/.ssh/id_ed25519`, not the home directory spelled out."""
    try:
        return f"~/{Path(path).relative_to(Path.home())}"
    except (TypeError, ValueError):
        return str(path)


def display(setting, value):
    """The text help, prompts and docs show for a value - None for one there's nothing to show of."""
    if value is None or value is UNSET or value == "" or value == ():
        return None
    if setting.multiple:
        return ", ".join(_display_one(setting, item) for item in value)
    return _display_one(setting, value)


def _display_one(setting, value):
    if setting.choices is not None and not setting.is_bool():
        labels = [label for label, choice in setting.choices.items() if choice == value]
        if labels:
            return labels[0]
    if isinstance(value, Path):
        return display_path(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return _("yes") if value else _("no")
    return str(value)


def hint(setting):
    """` (1-64, soft max 8)` - a setting's limits, as help and questions show them."""
    parts = []
    if setting.min is not None and setting.max is not None:
        parts.append(f"{setting.min}-{setting.max}")
    elif setting.min is not None:
        parts.append(f">= {setting.min}")
    elif setting.max is not None:
        parts.append(f"<= {setting.max}")
    if setting.soft_max is not None:
        parts.append(_("soft max %(soft_max)s") % {"soft_max": setting.soft_max})
    return f" ({', '.join(parts)})" if parts else ""
