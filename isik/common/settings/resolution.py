"""
Resolving a `Settings` class into an instance: each setting from the first place that has it - option,
environment variable, answer, current value, default - with where it came from kept beside it.

Nothing here knows about click: options arrive as `{key: (value, Source)}`, and questions go through
whatever `asker` the caller hands over (isik.cli's, in a terminal).
"""

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace

from isik.common.config.environment import FILE_SUFFIX, read_variable
from isik.common.config.exceptions import ConfigError
from isik.common.settings.conversion import Invalid, convert, display, expand
from isik.common.settings.declarations import (
    GIVEN,
    KEEP_CURRENT,
    REQUIRED,
    UNSET,
    Constant,
    Group,
    SelectedGroup,
    Setting,
    Source,
)
from isik.common.settings.errors import SettingsAborted, SettingsError
from isik.common.settings.exemptions import NoEnv, NoOption
from isik.common.settings.messages import Message, default_template


@dataclass(frozen=True)
class Entry:
    """A declaration where one command puts it: its path from the root class, and its names."""

    declaration: object
    owner: type
    path: tuple
    env_prefix: str
    option_prefix: str
    # The NoEnv of a class this one is nested in, which covers it unless it says otherwise.
    outer_env: object = None

    @property
    def name(self):
        return self.declaration.name

    @property
    def key(self):
        """The one name an entry goes by everywhere - `compression_settings__level`."""
        return "__".join(self.path)

    @property
    def class_env(self):
        """The NoEnv covering this entry's class - its own, or the one it's nested under."""
        own = self.owner.settings_options()["env"]
        return self.outer_env if own is None else own

    @property
    def env_name(self):
        """The variable a setting is read from - None for one with no variable, and for a group or constant."""
        setting = self.declaration
        if not isinstance(setting, Setting) or self.env_exemption is not None:
            return None
        if isinstance(setting.env, str):  # after the exemption, which is a str too
            return setting.env
        return self.env_prefix + setting.name.upper()

    @property
    def env_exemption(self):
        setting = self.declaration
        if not isinstance(setting, Setting):
            return None
        if isinstance(setting.env, NoEnv):
            return setting.env
        if setting.env is None:
            return self.class_env
        return None

    @property
    def option_names(self):
        """The options a setting is given by - None for one with no option, and for a group or constant."""
        if not isinstance(self.declaration, Setting):
            return None
        option = self.declaration.option
        if isinstance(option, NoOption):
            return None
        if isinstance(option, str):
            return [option]
        if option is not None:
            return list(option)
        return ["--" + self.option_prefix + self.name.replace("_", "-")]

    @property
    def option_file_name(self):
        setting = self.declaration
        wanted = setting.option_file
        if wanted is None:
            wanted = self.owner.settings_options()["option_file"]
        names = self.option_names
        if not wanted or not names:
            return None
        return max(names, key=len) + "-file"

    def label(self, source=None):
        """How an error names the setting - the way it was given: `--port`, `APP_PORT`, `APP_PORT_FILE`."""
        if source in (Source.ENV, Source.CURRENT) and self.env_name:
            return self.env_name
        if source is Source.ENV_FILE and self.env_name:
            return self.env_name + FILE_SUFFIX
        if source is Source.OPTION_FILE and self.option_file_name:
            return self.option_file_name
        if self.option_names:
            return max(self.option_names, key=len)
        return self.env_name or ".".join(self.path)


def declarations(cls):
    """`{name: declaration}` in declaration order, a subclass's after its bases'."""
    return cls.__dict__["_declarations"]


def ordered(cls):
    """The declarations in the order they're asked: `ask_order=` first, then the rest as declared."""
    found = declarations(cls)
    first = [name for name in cls.settings_options()["ask_order"] if name in found]
    return [found[name] for name in first] + [item for name, item in found.items() if name not in first]


def entry(cls, declaration, path, env_prefix, option_prefix, outer_env=None):
    return Entry(declaration, cls, (*path, declaration.name), env_prefix, option_prefix, outer_env)


def nested(here, group):
    """(env prefix, option prefix, NoEnv) for the settings of `group`, nested where `here` is."""
    return here.env_prefix + group.prefix, here.option_prefix + group.option_prefix, here.class_env


def walk(cls, selection, *, env_prefix=None, option_prefix="", path=(), outer_env=None):
    """
    Every entry one selection of `cls` has, in declaration order - help's order - with each group's
    entries where the group sits. `selection` is `{selector path: chosen key}`.
    """
    if env_prefix is None:
        env_prefix = cls.settings_options()["prefix"]
    for declaration in declarations(cls).values():
        here = entry(cls, declaration, path, env_prefix, option_prefix, outer_env)
        yield here
        if isinstance(declaration, Group | SelectedGroup):
            group = chosen(declaration, selection, path)
            group_env, group_option, group_outer = nested(here, group)
            yield from walk(
                group.settings_class,
                selection,
                env_prefix=group_env,
                option_prefix=group_option,
                path=here.path,
                outer_env=group_outer,
            )


def chosen(declaration, selection, path):
    """The group a Group is, or the one its selector picked in `selection`."""
    if isinstance(declaration, Group):
        return declaration
    selector = declaration.selector
    return selector.groups[selection[(*path, selector.name)]]


def selectors(cls, *, path=()):
    """(path, selector) for every selector `cls` can reach, whichever groups are chosen."""
    for declaration in declarations(cls).values():
        if isinstance(declaration, Setting) and hasattr(declaration, "groups"):
            yield (*path, declaration.name), declaration
        if isinstance(declaration, Group):
            yield from selectors(declaration.settings_class, path=(*path, declaration.name))
        if isinstance(declaration, SelectedGroup):
            for group in declaration.selector.groups.values():
                yield from selectors(group.settings_class, path=(*path, declaration.name))


def default_selection(cls):
    return {path: default_choice(selector) for path, selector in selectors(cls)}


def default_choice(selector):
    if selector.default is not REQUIRED:
        return selector.default
    return next(iter(selector.groups))


def every_selection(cls):
    """Each combination of choices `cls`'s selectors allow - what a check over all of them walks."""
    combinations = [{}]
    for path, selector in selectors(cls):
        combinations = [{**combination, path: key} for combination in combinations for key in selector.groups]
    return combinations


def names_of(entry):
    """Every name a setting is given by - its options and variables, the negated and file forms included."""
    setting = entry.declaration
    names = list(entry.option_names or ())
    if setting.is_bool() and setting.negatable and names:
        names.append("--no-" + max(names, key=len)[2:])
    if entry.option_file_name:
        names.append(entry.option_file_name)
    if entry.env_name is not None:
        names.append(entry.env_name)
        if setting.env_file:
            names.append(entry.env_name + FILE_SUFFIX)
    return names


def clashes(cls, *, env_prefix=None, taken=()):
    """
    `(setting, other, name)` for each name two settings would share in some selection - `other` is None
    for a name in `taken`, one the command has already. A variable read as two settings' is never meant.
    """
    found = []
    for selection in every_selection(cls):
        owners = dict.fromkeys(taken)
        for here in walk(cls, selection, env_prefix=env_prefix):
            if not isinstance(here.declaration, Setting):
                continue
            for name in names_of(here):
                if name not in owners:
                    owners[name] = here.key
                elif (here.key, owners[name], name) not in found:
                    found.append((here.key, owners[name], name))
    return found


def has_value(value):
    return value is not None and value is not UNSET and value is not False and value != "" and value != ()


def default_of(setting):
    default = setting.default
    if callable(default) and not isinstance(default, type | Enum):
        default = default()
    return tuple(default) if setting.multiple and isinstance(default, list) else default


@dataclass
class Resolution:
    """One resolve: where values come from, and what it has found so far."""

    root: type
    options: dict = field(default_factory=dict)
    environ: object = field(default_factory=dict)
    current: object = None
    asker: object = None
    included: object = None
    prefix: object = None
    soft: list = field(default_factory=list)
    # Every entry resolved so far, by path - how a later message names one.
    entries: dict = field(default_factory=dict)

    def message(self, owner, kind, /, **params):
        """The text of message `kind` - as the setting's class words it, else the root's, else isik's."""
        template = owner.settings_options()["messages"].get(kind)
        if template is None:
            template = self.root.settings_options()["messages"].get(kind, default_template(kind))
        return template % params

    def fail(self, entry, kind, /, *, source=None, **params):
        return SettingsError(self.message(entry.owner, kind, name=entry.label(source), **params))

    def invalid(self, entry, source, text, error):
        setting = entry.declaration
        show = setting.show_value
        if show is None:
            show = entry.owner.settings_options()["show_values"]
        if setting.secret or not show:
            return self.fail(entry, Message.INVALID_SECRET, source=source)
        return self.fail(entry, Message.INVALID, source=source, error=error, value=text)

    def resolve(self):
        prefix = self.root.settings_options()["prefix"] if self.prefix is None else self.prefix
        instance = self.resolve_class(self.root, (), prefix, "")
        self.check_soft(instance)
        return instance

    def resolve_class(self, cls, path, env_prefix, option_prefix, outer_env=None):
        values, sources, namespace = {}, {}, SimpleNamespace()
        entries = {}
        for declaration in ordered(cls):
            here = entry(cls, declaration, path, env_prefix, option_prefix, outer_env)
            entries[declaration.name] = self.entries[here.path] = here
            if isinstance(declaration, Constant):
                value, source = declaration.value, Source.CONSTANT
            elif isinstance(declaration, Group | SelectedGroup):
                group = declaration
                if isinstance(declaration, SelectedGroup):
                    group = declaration.selector.groups[values[declaration.selector.name]]
                instance = self.resolve_class(group.settings_class, here.path, *nested(here, group))
                value, source = group.built(instance), Source.DEFAULT
            else:
                value, source = self.resolve_setting(here, namespace)
            values[declaration.name] = value
            sources[declaration.name] = source
            setattr(namespace, declaration.name, value)
        self.check_relations(entries, values, sources)
        return construct(cls, values, sources, entries)

    def is_included(self, here):
        return self.included is None or self.included(here)

    def read_env(self, here):
        setting = here.declaration
        if here.env_name is None:
            return None
        try:
            found = read_variable(self.environ, here.env_name, files=setting.env_file)
        except ConfigError as error:
            raise SettingsError(str(error)) from None
        if found is None:
            return None
        text, from_file = found
        source = Source.ENV_FILE if from_file else Source.ENV
        try:
            return convert(setting, text), source
        except Invalid as error:
            raise self.invalid(here, source, text, str(error)) from None

    def read_current(self, here):
        """`(value, text)` for a current value - a pair, since a caster may make one into None - or None."""
        if self.current is None or here.env_name is None or here.env_name not in self.current:
            return None
        text = self.current[here.env_name]
        try:
            return convert(here.declaration, text), text
        except Invalid as error:
            raise self.invalid(here, Source.CURRENT, text, str(error)) from None

    def read_option(self, here):
        found = self.options.get(here.key)
        if found is None:
            return None
        value, source = found
        setting = here.declaration
        if source is Source.OPTION_FILE:
            try:
                value = convert(setting, value)
            except Invalid as error:
                raise self.invalid(here, source, value, str(error)) from None
        elif setting.choices is not None and not setting.is_bool():
            # click has already refused anything that isn't one of the labels.
            value = tuple(setting.choices[item] for item in value) if setting.multiple else setting.choices[value]
        return value, source

    def resolve_setting(self, here, namespace):
        setting = here.declaration
        if not self.is_included(here):
            return (UNSET if setting.default is REQUIRED else default_of(setting)), Source.DEFAULT
        given = self.read_option(here)
        environment = self.read_env(here)
        if setting.only_when is not None and not setting.only_when.test(namespace):
            outside = given or environment
            if outside is not None:
                template = setting.only_when.message
                name = here.label(outside[1])
                raise SettingsError(
                    template % {"name": name}
                    if template
                    else self.message(here.owner, Message.NOT_APPLICABLE, name=name)
                )
            return (UNSET if setting.default is REQUIRED else default_of(setting)), Source.DEFAULT
        current = self.read_current(here)
        if given is not None:
            value, source = given
        elif environment is not None:
            value, source = environment
        elif self.asker is not None and setting.asked and (setting.ask_when is None or setting.ask_when(namespace)):
            value, source = self.ask(here, current), Source.QUESTION
        elif current is not None:
            (value, _text), source = current, Source.CURRENT
        elif setting.default is not REQUIRED:
            value, source = default_of(setting), Source.DEFAULT
        else:
            raise self.fail(here, Message.REQUIRED)
        try:
            value = expand(value)
        except RuntimeError:  # pathlib's word for a process with no home directory - common in a container
            raise self.fail(here, Message.NO_HOME, source=source, variable=here.env_name or here.label()) from None
        if source is not Source.QUESTION:
            error = self.hard_error(here, value, source)
            if error:
                raise SettingsError(error)
        if setting.required and (value == "" or value == ()):
            raise self.fail(here, Message.EMPTY, source=source)
        self.note_soft(here, value, source)
        return value, source

    def ask(self, here, current):
        setting = here.declaration
        if current is not None:
            default, text = current
        else:
            default, text = (default_of(setting) if setting.default is not REQUIRED else None), None
        while True:
            value = self.asker.ask(here, default, text)
            if value in ("", ()) and setting.on_empty is not REQUIRED:
                if setting.on_empty is KEEP_CURRENT:
                    value = current[0] if current is not None else value
                elif callable(setting.on_empty):
                    value = setting.on_empty()
                else:
                    value = setting.on_empty
            elif value == "" and setting.default is UNSET:
                value = UNSET
            error = self.hard_error(here, value)
            if not error:
                return value
            self.asker.tell(self.message(here.owner, Message.ASKED_ERROR, message=error))

    def hard_error(self, here, value, source=None):
        setting = here.declaration
        items = value if setting.multiple else (value,)
        for item in [item for item in items if item is not None and item is not UNSET]:
            if setting.min is not None and item < setting.min:
                return self.message(here.owner, Message.BELOW_MIN, name=here.label(source), min=setting.min)
            if setting.max is not None and item > setting.max:
                return self.message(here.owner, Message.ABOVE_MAX, name=here.label(source), max=setting.max)
        return None

    def note_soft(self, here, value, source):
        setting = here.declaration
        if setting.soft_max is None:
            return
        for item in value if setting.multiple else (value,):
            if item is not None and item is not UNSET and item > setting.soft_max:
                self.soft.append((here, item, source))
                return

    def check_soft(self, instance):
        asked = []
        for here, value, source in self.soft:
            message = self.message(
                here.owner,
                Message.ABOVE_SOFT_MAX,
                name=here.label(source),
                value=value,
                soft_max=here.declaration.soft_max,
            )
            if source is Source.QUESTION:
                asked.append(message)
                continue
            override = here.declaration.soft_override or self.root.settings_options()["soft_override"]
            if override is None:
                raise SettingsError(message)
            if has_value(getattr(instance, override)):
                continue
            override_label = self.entries[(override,)].label()
            raise SettingsError(
                self.message(here.owner, Message.SOFT_OVERRIDE, message=message, override=override_label)
            )
        if asked:
            header = self.message(self.root, Message.SOFT_HEADER)
            question = self.message(self.root, Message.SOFT_CONFIRM)
            if not self.asker.confirm_soft(header, asked, question):
                raise SettingsAborted()

    def check_relations(self, entries, values, sources):
        for name, here in entries.items():
            setting = here.declaration
            if not isinstance(setting, Setting) or not has_value(values[name]):
                continue
            for requires in setting.requires:
                if not has_value(values[requires.other]):
                    raise self.relation(here, requires, entries, sources, Message.REQUIRES)
            for excludes in setting.excludes:
                if sources[excludes.other] in GIVEN:
                    raise self.relation(here, excludes, entries, sources, Message.EXCLUDES)

    def relation(self, here, relation, entries, sources, kind):
        other = entries[relation.other]
        params = {"name": here.label(sources[here.name]), "other": other.label(sources[relation.other])}
        text = relation.message % params if relation.message else self.message(here.owner, kind, **params)
        return SettingsError(text)


def construct(cls, values, sources, entries):
    """An instance of `cls` holding `values`, knowing where each came from - `check()` runs as it's made."""
    instance = cls.__new__(cls)
    object.__setattr__(instance, "_sources", dict(sources))
    object.__setattr__(instance, "_entries", dict(entries))
    fields = {field.name for field in dataclasses.fields(cls)}
    cls.__init__(instance, **{name: value for name, value in values.items() if name in fields})
    return instance


def label_of(instance, name):
    """How an error about `instance`'s setting `name` names it - the way that value was given."""
    resolved = instance.__dict__.get("_entries")
    if resolved is not None:  # its names as the command it came from gave them, a group's prefix included
        return resolved[name].label(instance.source(name))
    cls = type(instance)
    return entry(cls, declarations(cls)[name], (), cls.settings_options()["prefix"], "").label()


def display_default(setting):
    """What help and docs show as a setting's default - `shown=` if it has one."""
    if setting.shown is not None:
        return setting.shown
    if setting.secret or setting.default is REQUIRED:
        return None
    return display(setting, default_of(setting))
