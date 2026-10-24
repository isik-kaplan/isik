import dataclasses
import os
from collections.abc import Mapping
from enum import Enum
from typing import Any

from isik._internal.translation import gettext as _
from isik.common.settings.declarations import (
    REQUIRED,
    UNSET,
    Constant,
    Declaration,
    Group,
    Prompt,
    SelectedGroup,
    Setting,
    Source,
)
from isik.common.settings.errors import SettingsError
from isik.common.settings.exemptions import NoEnv
from isik.common.settings.messages import Message


# What a class takes as keywords - `class Backup(Settings, prefix="BACKUP_", prompt=Prompt.CLICK)` - and
# what a class that doesn't say gets. A subclass keeps whatever its base said.
CLASS_OPTIONS = {
    "prefix": "",
    "prompt": None,
    "env": None,
    "option_file": False,
    "show_values": True,
    "messages": {},
    "soft_override": None,
    "ask_order": (),
}
# A setting can't be named after what every instance has.
RESERVED = frozenset({"source", "sources", "as_dict", "error", "check", "replace", "from_env", "settings_options"})


def _class_option(name, value):
    if name == "env" and value is not None and not isinstance(value, NoEnv):
        raise TypeError(_("A Settings class's env= takes NoEnv(reason=...), not %(value)r.") % {"value": value})
    if name == "prompt" and value is not None and not isinstance(value, Prompt):
        raise TypeError(_("A Settings class's prompt= takes a Prompt, not %(value)r.") % {"value": value})
    if name == "messages":
        unknown = [key for key in value if not isinstance(key, Message)]
        if unknown:
            raise TypeError(_("messages= is keyed by Message, not %(keys)r.") % {"keys": unknown})
        return dict(value)
    return value


def _field(declaration):
    """The dataclass field a declaration becomes."""
    metadata = {"isik.settings": declaration}
    if isinstance(declaration, Group | SelectedGroup):
        return dataclasses.field(default_factory=_default_group(declaration), metadata=metadata)
    default = declaration.default
    repr_ = not declaration.secret
    if default is REQUIRED:
        return dataclasses.field(repr=repr_, metadata=metadata)
    if callable(default) and not isinstance(default, type | Enum):
        return dataclasses.field(default_factory=default, repr=repr_, metadata=metadata)
    return dataclasses.field(default=default, repr=repr_, metadata=metadata)


def _default_group(declaration):
    if isinstance(declaration, SelectedGroup):
        selector = declaration.selector
        key = selector.default if selector.default is not REQUIRED else next(iter(selector.groups))
        group = selector.groups[key]
    else:
        group = declaration
    return lambda: group.built(group.settings_class())


def refuse_clashes(found, where):
    """A TypeError naming each name two settings would share - see `clashes()`."""
    if not found:
        return
    lines = [
        _("%(setting)s and %(other)s would both be %(name)s") % {"setting": setting, "other": other, "name": name}
        if other is not None
        else _("%(setting)s would be %(name)s, which the command already is") % {"setting": setting, "name": name}
        for setting, other, name in found
    ]
    raise TypeError(
        _("%(where)s gives two things one name - rename one:\n%(lines)s") % {"where": where, "lines": "\n".join(lines)}
    )


def _selectors_first(cls, found, order):
    """A selector is read before its group is reached - in help, in questions, and when peeking."""
    for name, value in found.items():
        if not isinstance(value, SelectedGroup):
            continue
        if value.selector.name not in found:
            raise TypeError(
                _("%(type)s.%(name)s holds the settings of a selector %(type)s doesn't declare.")
                % {"type": cls.__name__, "name": name}
            )
        if value.selector.name not in order[: order.index(name)]:
            raise TypeError(
                _("%(type)s.%(name)s comes before its selector %(selector)s - declare the selector first.")
                % {"type": cls.__name__, "name": name, "selector": value.selector.name}
            )


class Settings:
    """
    Settings declared once, as class attributes - each given by option, environment variable or answer:

        class Backup(Settings, prefix="BACKUP_"):
            target = Setting(Path, "Where to write the backup", default=lambda: Path.home() / "backups")
            encrypt = Setting(bool, "Encrypt the backup?", default=False)

        Backup.from_env()                       # from the environment alone
        Backup(target=Path("/srv"))             # in code - a frozen dataclass underneath
        backup.source("target")                 # Source.ENV, Source.DEFAULT, ...

    `isik.cli.options(Backup)` puts them on a click command. Keywords on the class (`prefix`, `prompt`,
    `env`, `option_file`, `show_values`, `messages`, `soft_override`, `ask_order`) set its defaults - see
    docs/common/settings.
    """

    def __init_subclass__(cls, **keywords):
        own = {name: keywords.pop(name) for name in list(keywords) if name in CLASS_OPTIONS}
        super().__init_subclass__(**keywords)
        options = dict(cls.settings_options()) if hasattr(cls, "_settings_options") else dict(CLASS_OPTIONS)
        options.update({name: _class_option(name, value) for name, value in own.items()})
        cls._settings_options = options

        found = {}
        for base in reversed(cls.__mro__[1:]):
            found.update(base.__dict__.get("_declarations", {}))
        annotations = dict(cls.__dict__.get("__annotations__", {}))
        for name, value in list(cls.__dict__.items()):
            if not isinstance(value, Declaration):
                continue
            if name in RESERVED:
                raise TypeError(
                    _("%(type)s.%(name)s is the name of a Settings method - name the setting something else.")
                    % {"type": cls.__name__, "name": name}
                )
            found[name] = value
            if isinstance(value, Constant):
                # A plain class attribute, not a field: nothing passes it, nothing replaces it.
                setattr(cls, name, value.value)
                continue
            annotations[name] = value.annotation() if isinstance(value, Setting) else Any
            setattr(cls, name, _field(value))
        cls._declarations = found
        from isik.common.settings.resolution import clashes, ordered

        # Help's order, then the questions'.
        for order in (list(found), [declaration.name for declaration in ordered(cls)]):
            _selectors_first(cls, found, order)
        cls.__annotations__ = annotations
        dataclasses.dataclass(frozen=True, kw_only=True)(cls)
        refuse_clashes(clashes(cls), cls.__name__)

    @classmethod
    def settings_options(cls) -> Mapping[str, Any]:
        """The class's keywords - its own, or what it inherited."""
        return cls._settings_options

    def __post_init__(self):
        self.check()

    def check(self) -> None:
        """Checks across settings, run whenever an instance is made - raise `self.error(...)`."""

    @classmethod
    def from_env(cls, environ=None, *, prefix=None, current=None):
        """
        An instance from environment variables alone - no options, no questions. `environ` defaults to
        `os.environ`; `prefix` replaces the class's; `current` is a further `{variable: text}` layer read
        after the environment, the values a service already has.
        """
        from isik.common.settings.resolution import Resolution

        return Resolution(
            cls, environ=os.environ if environ is None else environ, current=current, prefix=prefix
        ).resolve()

    def source(self, name: str) -> Source:
        """Where setting `name`'s value came from - `Source.CODE` for one passed in code."""
        return self.__dict__.get("_sources", {}).get(name, Source.CODE)

    def sources(self, name: str) -> tuple:
        """One source per item of a multiple setting - each from wherever the whole value came."""
        value = getattr(self, name)
        return tuple(self.source(name) for _item in value)

    def as_dict(self) -> dict:
        """Every value, groups' included, in one flat dict - unset ones left out."""
        from isik.common.settings.resolution import declarations

        flat = {}
        for name, declaration in declarations(type(self)).items():
            value = getattr(self, name)
            if isinstance(declaration, Group | SelectedGroup):
                flat.update(value.as_dict() if isinstance(value, Settings) else dataclasses.asdict(value))
            elif value is not UNSET:
                flat[name] = value
        return flat

    def replace(self, **changes):
        return dataclasses.replace(self, **changes)

    def error(self, name_or_message: str, message: str | None = None) -> SettingsError:
        """
        The error for `check()` to raise - `self.error("comment", "must not contain newlines")` names the
        setting the way it was given (`--comment must not contain newlines`); a single argument is the
        whole message.
        """
        from isik.common.settings.resolution import label_of

        if message is None:
            return SettingsError(name_or_message)
        # The setting's name, then the app's own words about it - "--comment must not contain newlines".
        text = _("%(name)s %(message)s") % {"name": label_of(self, name_or_message), "message": message}
        return SettingsError(text)
