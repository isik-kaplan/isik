"""
What a `Settings` class is declared with - each a class attribute, in the order they're asked and shown:

    class Backup(Settings, prefix="BACKUP_"):
        target = Setting(Path, "Where to write the backup", default=lambda: Path.home() / "backups")
        level = Setting(int, "Compression level", default=3, min=1, max=22, soft_max=19)
"""

import dataclasses
import typing
from collections.abc import Callable, Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar, overload

from isik._internal.translation import gettext as _
from isik.common.settings.exemptions import QUESTION_EXEMPTIONS, IsConstant, NoEnv, NoOption, NoQuestion
from isik.common.utils.exemptions import opted_out
from isik.common.utils.sentinel import Sentinel


T = TypeVar("T")

REQUIRED = Sentinel("SETTING_REQUIRED")
# A setting with no value at all - left out rather than sent, the way a client leaves a server its default.
UNSET = Sentinel("SETTING_UNSET")
# `on_empty=KEEP_CURRENT`: an empty answer keeps the value `current=` holds.
KEEP_CURRENT = Sentinel("SETTING_KEEP_CURRENT")
# `separator=LINES`: one value per line, for values that may hold the comma.
LINES = Sentinel("SETTING_LINES")


class Source(Enum):
    """Where a resolved value came from."""

    OPTION = "option"
    OPTION_FILE = "option-file"
    ENV = "env"
    ENV_FILE = "env-file"
    QUESTION = "question"
    CURRENT = "current"
    DEFAULT = "default"
    CONSTANT = "constant"
    # Passed in code - `Backup(target=...)`, `dataclasses.replace()`.
    CODE = "code"


GIVEN = frozenset({Source.OPTION, Source.OPTION_FILE, Source.ENV, Source.ENV_FILE})


class Prompt(Enum):
    """How a question is asked: click's line prompts, or questionary's arrow-key ones."""

    CLICK = "click"
    QUESTIONARY = "questionary"


class Requires:
    """`requires=Requires("label", message=...)` - when this setting has a value, `other` must have one too."""

    def __init__(self, other, *, message=None):
        self.other = other
        self.message = message


class Excludes:
    """`excludes=Excludes("output", message=...)` - when this setting has a value, `other` can't be given."""

    def __init__(self, other, *, message=None):
        self.other = other
        self.message = message


class OnlyWhen:
    """`only_when=OnlyWhen(lambda values: values.encrypt, message=...)` - the setting exists only when it passes."""

    def __init__(self, test, *, message=None):
        if not callable(test):
            raise TypeError(_("OnlyWhen takes a function of the values so far, not %(value)r.") % {"value": test})
        self.test = test
        self.message = message


def without(value, exemption_type):
    """
    A setting's `option=False`, `env=False` or `question=False` as the exemption it stands for - taken when
    the rule isn't strict, and refused naming how to loosen it when it is. Anything else is as given.
    """
    if value is not False:
        return value
    exemption = opted_out(False, exemption_type, plain=False)
    if exemption is None:
        raise TypeError(
            _(
                "False opts out of %(rule)s without a reason, which needs Requirement.OPTIONAL for it - "
                "or write %(type)s(reason=...)."
            )
            % {"rule": exemption_type.rule, "type": exemption_type.__name__}
        )
    return exemption


def _relations(value, kind):
    if isinstance(value, str | kind):
        value = (value,)
    return tuple(item if isinstance(item, kind) else kind(item) for item in value)


class Declaration:
    """A class attribute a `Settings` class collects - bound to the name it's assigned to."""

    name = None

    def __set_name__(self, owner, name):
        self.name = name


class Setting(Declaration, Generic[T]):
    """
    One setting, given by option, environment variable or answer. `kind` is a type (`str`, `int`, `float`,
    `bool`, `Path`, an `Enum`), a `str -> value` caster, or a click `ParamType`. Everything else is
    inferred and can be overridden - see docs/common/settings.
    """

    def __init__(
        self,
        kind: Any,
        help: str,
        *,
        default: Any = REQUIRED,
        shown: str | None = None,
        required: bool = False,
        secret: bool = False,
        confirm: bool = False,
        multiple: bool = False,
        separator: Any = ",",
        bare: Any = REQUIRED,
        choices: Any = None,
        min: Any = None,
        max: Any = None,
        soft_max: Any = None,
        soft_override: str | None = None,
        option: Any = None,
        env: Any = None,
        option_file: bool | None = None,
        env_file: bool = True,
        negatable: bool = True,
        metavar: str | None = None,
        question: Any = None,
        prompt: Prompt | None = None,
        ask_when: Callable[[Any], bool] | None = None,
        only_when: Any = None,
        requires: Any = (),
        excludes: Any = (),
        on_empty: Any = REQUIRED,
        before_ask: Callable[[Any], None] | None = None,
        show_value: bool | None = None,
    ):
        if required and default is not REQUIRED:
            raise TypeError(_("A required setting has no default - drop default= or required=True."))
        option, env, question = without(option, NoOption), without(env, NoEnv), without(question, NoQuestion)
        if option is not None and not isinstance(option, str | tuple | list | NoOption):
            raise TypeError(
                _("option= takes the option's name, or NoOption(reason=...), not %(value)r.") % {"value": option}
            )
        if env is not None and not isinstance(env, str | NoEnv):
            raise TypeError(
                _("env= takes the variable's full name, or NoEnv(reason=...), not %(value)r.") % {"value": env}
            )
        if question is not None and not isinstance(question, (str, *QUESTION_EXEMPTIONS)):
            raise TypeError(
                _("question= takes the question's text, or an exemption such as NoQuestion(reason=...), not %(value)r.")
                % {"value": question}
            )
        if prompt is not None and not isinstance(prompt, Prompt):
            raise TypeError(_("prompt= takes a Prompt, not %(value)r.") % {"value": prompt})
        if only_when is not None and not isinstance(only_when, OnlyWhen):
            only_when = OnlyWhen(only_when)
        self.kind = kind
        self.help = help
        self.default = tuple(default) if multiple and isinstance(default, list) else default
        self.shown = shown
        self.required = required or default is REQUIRED
        self.secret = secret
        self.confirm = confirm
        self.multiple = multiple
        self.separator = separator
        self.bare = bare
        self.choices = _choices(kind, choices)
        self.min, self.max, self.soft_max = min, max, soft_max
        self.soft_override = soft_override
        self.option = option
        self.env = env
        self.option_file = option_file
        self.env_file = env_file
        self.negatable = negatable
        self.metavar = metavar
        self.question = question
        self.prompt = prompt
        self.ask_when = ask_when
        self.only_when = only_when
        self.requires = _relations(requires, Requires)
        self.excludes = _relations(excludes, Excludes)
        self.on_empty = on_empty
        self.before_ask = before_ask
        self.show_value = show_value

    @property
    def asked(self):
        """False when an exemption stands in for the question."""
        return not isinstance(self.question, QUESTION_EXEMPTIONS)

    def question_text(self):
        # An exemption is a str too - its text is a reason, not a question.
        return self.question if self.asked and isinstance(self.question, str) else self.help

    def is_bool(self):
        return self.kind is bool

    def annotation(self):
        hint = self.kind if isinstance(self.kind, type) else Any
        return tuple[hint, ...] if self.multiple else hint

    def __repr__(self):
        return f"<{type(self).__name__} {self.name}>"

    if TYPE_CHECKING:  # pragma: no cover - read by editors, never run

        @overload
        def __get__(self, instance: None, owner: type) -> "Setting[T]": ...
        @overload
        def __get__(self, instance: object, owner: type) -> T: ...
        def __get__(self, instance, owner): ...


def _choices(kind, choices):
    """`{label: value}` for a setting picked from a list - its Enum's members, or `choices=`."""
    if choices is None:
        if isinstance(kind, type) and issubclass(kind, Enum):
            return {str(member.value): member for member in kind}
        return None
    if isinstance(choices, Mapping):
        return dict(choices)
    return {str(choice.value) if isinstance(choice, Enum) else str(choice): choice for choice in choices}


class Constant(Declaration):
    """`hash_len = Constant(32, reason="...")` - fixed on purpose, so neither given nor asked."""

    def __init__(self, value, *, reason=REQUIRED):
        self.value = value
        # No reason is IsConstant's own call to refuse - it takes none only when its rule isn't strict.
        self.reason = IsConstant() if reason is REQUIRED else IsConstant(reason=reason)


class Group(Declaration):
    """
    Another `Settings` class's settings, nested under this attribute - `lock = Group(LockSettings,
    prefix="LOCK_")` reads `APP_LOCK_EXPIRES` and gives `--lock-expires`.
    """

    def __init__(self, settings_class, *, prefix="", option_prefix=None, target=None):
        from isik.common.settings.base import Settings

        if not (isinstance(settings_class, type) and issubclass(settings_class, Settings)):
            raise TypeError(
                _("Group takes a Settings class - use Group.from_dataclass() for a plain dataclass, not %(value)r.")
                % {"value": settings_class}
            )
        self.settings_class = settings_class
        self.prefix = prefix
        self.option_prefix = prefix.lower().replace("_", "-") if option_prefix is None else option_prefix
        # The plain dataclass the values are handed over as, for one made by from_dataclass().
        self.target = target

    def built(self, instance):
        if self.target is None:
            return instance
        fields = dataclasses.fields(self.target)
        return self.target(**{field.name: getattr(instance, field.name) for field in fields if field.init})

    @classmethod
    def from_dataclass(cls, dataclass, *, prefix="", option_prefix=None, describe=None):
        """
        A group over a plain dataclass, for code that can't depend on isik - its values come back as an
        instance of that dataclass. A field's `metadata` gives `help`, `secret` and `shown`, or
        `describe(field)` returns the `Setting` keywords for it.
        """
        from isik.common.settings.base import Settings

        if not dataclasses.is_dataclass(dataclass):
            raise TypeError(_("from_dataclass() takes a dataclass, not %(value)r.") % {"value": dataclass})
        hints = typing.get_type_hints(dataclass)
        body = {}
        for field in dataclasses.fields(dataclass):
            if not field.init:
                continue
            kind, multiple, default = _from_hint(hints[field.name], field)
            keywords = {key: field.metadata[key] for key in ("help", "secret", "shown") if key in field.metadata}
            keywords = {"default": default, "multiple": multiple, **keywords, **(describe(field) if describe else {})}
            body[field.name] = Setting(kind, keywords.pop("help", ""), **keywords)
        settings_class = type(dataclass.__name__, (Settings,), {"__module__": dataclass.__module__, **body})
        return cls(settings_class, prefix=prefix, option_prefix=option_prefix, target=dataclass)


def _from_hint(hint, field):
    if field.default is not dataclasses.MISSING:
        default = field.default
    elif field.default_factory is not dataclasses.MISSING:
        factory = field.default_factory
        default = lambda: factory()  # noqa: E731 - a factory may be a type, which a default never calls
    else:
        default = REQUIRED
    arguments = [argument for argument in typing.get_args(hint) if argument is not type(None)]
    if typing.get_origin(hint) is tuple:
        return arguments[0], True, default
    if len(arguments) == 1 and type(None) in typing.get_args(hint):  # X | None
        return arguments[0], False, default
    return hint, False, default


class Selector(Setting):
    """
    A setting whose value picks which group of settings applies - `kdf = Selector("KDF backend",
    {"scrypt": ScryptSettings, ...}, default="scrypt")`, with `kdf_settings = kdf.settings` placed where
    the chosen group's settings are asked and shown.
    """

    def __init__(self, help, groups, *, default=REQUIRED, **keywords):
        self.groups = {key: group if isinstance(group, Group) else Group(group) for key, group in dict(groups).items()}
        labels = {str(key.value) if isinstance(key, Enum) else str(key): key for key in self.groups}
        kind = type(next(iter(self.groups))) if self.groups else str
        super().__init__(kind, help, default=default, choices=labels, **keywords)
        self.settings = SelectedGroup(self)

    def label(self, key):
        return next(label for label, value in self.choices.items() if value == key)


class SelectedGroup(Declaration):
    """The settings of whichever group a `Selector` picked."""

    def __init__(self, selector):
        self.selector = selector
