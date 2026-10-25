"""
`options(SettingsClass)` - a `Settings` class's options on a click command, and its values resolved before
the command runs. It goes above `@click.command()`, so it has the command to change:

    @options(Backup)
    @click.command()
    @click.argument("source")
    def main(source, settings: Backup): ...
"""

import functools
import os
from enum import Enum
from pathlib import Path

import click
from click.core import ParameterSource

from isik._internal.translation import gettext as _
from isik.cli.help import HelpFormat
from isik.cli.policies import FunctionPolicy, terminal_unless_no_input
from isik.cli.prompts import Asker, scripted
from isik.common.config.casters import natural_boolean
from isik.common.config.environment import read_file, read_variable
from isik.common.config.exceptions import ConfigError
from isik.common.settings.base import Settings, refuse_clashes
from isik.common.settings.conversion import Invalid, hint, item_converter
from isik.common.settings.declarations import REQUIRED, Selector, Setting, Source
from isik.common.settings.errors import SettingsAborted, SettingsError
from isik.common.settings.messages import Message, default_template
from isik.common.settings.resolution import Resolution, clashes, default_selection, walk


OPTION = "isik__"
OPTION_FILE = "isik_file__"
NO_INPUT = "isik_no_input"
INTERACTIVE = "isik.interactive"


class CasterType(click.ParamType):
    """A setting's own converter as a click type, so an option says what's wrong the way the variable does."""

    name = "text"

    def __init__(self, setting):
        self.setting = setting

    def convert(self, value, param, ctx):
        if not isinstance(value, str):
            return value
        try:
            return item_converter(self.setting)(value)
        except Invalid as error:
            self.fail(str(error))  # click names the option and the command itself


def click_type(setting):
    kind = setting.kind
    if isinstance(kind, click.ParamType):
        return kind
    if setting.choices is not None:
        return click.Choice(list(setting.choices))
    if kind is int:
        return click.INT
    if kind is float:
        return click.FLOAT
    if kind is str:
        return click.STRING
    if kind is Path:
        return click.Path(path_type=Path)
    return CasterType(setting)


class ErrorStyle(Enum):
    """How a command shows a value it can't use - `options(errors=...)`."""

    # click's usage error: the usage line, "Try '... --help' for help.", then "Error: <message>".
    USAGE = "usage"
    # The message alone, on stderr - for a command whose errors read as its own.
    MESSAGE = "message"


class MessageError(click.ClickException):
    """A usage error shown as its message alone, with the exit code a usage error has."""

    exit_code = 2

    def show(self, file=None):
        click.echo(self.format_message(), file=file, err=True)


class Binding:
    """One `options(...)` - which class, how its command asks and shows it."""

    def __init__(self, settings_class, *, only, exclude, prefix, interactive, help, epilog, current, name, errors):
        if not (isinstance(settings_class, type) and issubclass(settings_class, Settings)):
            raise TypeError(_("options() takes a Settings class, not %(value)r.") % {"value": settings_class})
        if not isinstance(errors, ErrorStyle):
            raise TypeError(_("options()'s errors= takes an ErrorStyle, not %(value)r.") % {"value": errors})
        self.errors = errors
        self.settings_class = settings_class
        self.only = None if only is None else frozenset(only)
        self.exclude = frozenset(exclude)
        self.prefix = settings_class.settings_options()["prefix"] if prefix is None else prefix
        if interactive is None:
            interactive = terminal_unless_no_input()
        elif not hasattr(interactive, "adds_no_input"):
            interactive = FunctionPolicy(interactive)
        self.interactive = interactive
        self.help = HelpFormat() if help is None else help
        self.epilog = epilog
        self.current = current
        self.name = name

    def includes(self, entry):
        top = entry.path[0]
        return (self.only is None or top in self.only) and top not in self.exclude

    def entries(self, selection):
        """The settings one selection gives this command, in help's order."""
        for entry in walk(self.settings_class, selection, env_prefix=self.prefix):
            if isinstance(entry.declaration, Setting) and self.includes(entry):
                yield entry

    def params(self, selection):
        found = [param for entry in self.entries(selection) for param in self.entry_params(entry)]
        if self.interactive.adds_no_input:
            found.append(
                click.Option(
                    ["--no-input", NO_INPUT],
                    is_flag=True,
                    help=_("Don't ask any questions - fail on a missing value instead."),
                )
            )
        return found

    def entry_params(self, entry):
        setting = entry.declaration
        names = entry.option_names
        if names is None:
            return []
        description, own_line = self.help.describe(setting, setting.help + hint(setting))
        keywords = {"default": None, "help": description, "metavar": setting.metavar}
        declarations = list(names)
        if setting.is_bool():
            keywords["is_flag"] = True
            if setting.negatable:
                longest = max(names, key=len)
                declarations = [name for name in names if name != longest] + [f"{longest}/--no-{longest[2:]}"]
        else:
            keywords["type"] = click_type(setting)
            keywords["multiple"] = setting.multiple
            if setting.bare is not REQUIRED:
                keywords.update(is_flag=False, flag_value=setting.bare)
        option = click.Option([*declarations, OPTION + entry.key], **keywords)
        option.isik_default = own_line
        found = [option]
        if entry.option_file_name:
            found.append(
                click.Option(
                    [entry.option_file_name, OPTION_FILE + entry.key],
                    metavar="FILE",
                    help=self.help.file_help(setting, max(names, key=len)),
                )
            )
        return found

    def peek(self, args):
        """
        Which group each selector picks, read from the arguments before click parses them - so the options
        it registers are the chosen group's. The last occurrence wins, as click's own does; a value that
        isn't a choice leaves the default, for click to refuse properly.
        """
        selection = default_selection(self.settings_class)
        # walk() reads the selection as it reaches each group, so a group's own selectors are the chosen one's.
        for entry in walk(self.settings_class, selection, env_prefix=self.prefix):
            selector = entry.declaration
            if isinstance(selector, Selector):
                label = _last(args, entry.option_names or ())
                if label is None and entry.env_name is not None:
                    label = _quietly_read(entry.env_name)
                selection[entry.path] = selector.choices.get(label, selection[entry.path])
        return selection


def _quietly_read(name):
    try:
        found = read_variable(os.environ, name)
    except ConfigError:
        return None  # reported properly when the value itself is read
    return None if found is None else found[0].strip()


def _last(args, names):
    """The value the last of `names` is given in `args` - skipping past each consumed value."""
    value = None
    arguments = iter(args)
    for argument in arguments:
        if argument in names:
            value = next(arguments, value)
            continue
        for name in names:
            if argument.startswith(name + "="):
                value = argument[len(name) + 1 :]
    return value


class SettingsCommandMixin:
    """What `options()` mixes into the command's own class."""

    def parse_args(self, ctx, args):
        binding = self.isik_binding
        selection = binding.peek(args)
        ctx.meta["isik.selection"] = selection
        self.params = [*binding.base_params, *binding.params(selection)]
        if binding.epilog is not None:
            self.epilog = binding.epilog({path[-1]: key for path, key in selection.items()})
        return super().parse_args(ctx, args)

    def format_options(self, ctx, formatter):
        self.isik_binding.help.format_options(self, ctx, formatter)
        # A group's own format_options lists its commands after its options - this replaces it, so says so too.
        if isinstance(self, click.Group):
            self.format_commands(ctx, formatter)


@functools.cache
def _command_class(base):
    return type(base.__name__, (SettingsCommandMixin, base), {"__module__": base.__module__})


def _given(ctx, binding, values):
    """`{key: (value, Source)}` for what options gave, files read - and whether `--no-input` was passed."""
    given = {}
    no_input = bool(values.pop(NO_INPUT, None))
    for name in list(values):
        if not name.startswith((OPTION, OPTION_FILE)):
            continue
        value = values.pop(name)
        if ctx.get_parameter_source(name) is not ParameterSource.COMMANDLINE:
            continue
        if name.startswith(OPTION_FILE):
            key = name.removeprefix(OPTION_FILE)
            option = next(param.opts[0] for param in ctx.command.params if param.name == name)
            if key in given:
                direct = next(max(param.opts, key=len) for param in ctx.command.params if param.name == OPTION + key)
                template = binding.settings_class.settings_options()["messages"].get(
                    Message.BOTH_GIVEN, default_template(Message.BOTH_GIVEN)
                )
                raise SettingsError(template % {"name": direct, "other": option})
            try:
                given[key] = (read_file(value, name=option), Source.OPTION_FILE)
            except ConfigError as error:
                raise SettingsError(str(error)) from None
        else:
            given[name.removeprefix(OPTION)] = (value, Source.OPTION)
    if not no_input and binding.interactive.adds_no_input:
        variable = binding.prefix + "NO_INPUT"
        if variable in os.environ:
            try:
                no_input = natural_boolean()(os.environ[variable])
            except ValueError:
                template = default_template(Message.INVALID)
                error = _("expected yes/no, true/false, on/off or 1/0")
                raise SettingsError(
                    template % {"name": variable, "error": error, "value": os.environ[variable]}
                ) from None
    return given, no_input


def _environment_sources(binding, selection):
    found = {}
    for entry in binding.entries(selection):
        if entry.env_name is None:
            continue
        try:
            read = read_variable(os.environ, entry.env_name, files=entry.declaration.env_file)
        except ConfigError:
            continue  # reported when the value is read
        if read is not None:
            found[entry.key] = Source.ENV_FILE if read[1] else Source.ENV
    return found


def _run(binding, callback, **values):
    ctx = click.get_current_context()
    try:
        given, no_input = _given(ctx, binding, values)
        selection = ctx.meta["isik.selection"]
        sources = {**_environment_sources(binding, selection), **{key: found[1] for key, found in given.items()}}
        interactive = scripted.get() is not None or binding.interactive(sources, no_input)
        ctx.meta[INTERACTIVE] = interactive
        resolution = Resolution(
            binding.settings_class,
            options=given,
            environ=os.environ,
            current=None if binding.current is None else binding.current(),
            included=binding.includes,
            prefix=binding.prefix,
        )
        if interactive:
            resolution.asker = Asker(binding.settings_class, resolution.message)
        settings = resolution.resolve()
    except SettingsError as error:
        if binding.errors is ErrorStyle.MESSAGE:
            raise MessageError(str(error)) from None
        raise click.UsageError(str(error)) from None  # click adds the usage line itself
    except SettingsAborted:
        raise click.Abort() from None
    return callback(**values, **{binding.name: settings})


def is_interactive():
    """
    Whether the running command asks its questions - its policy's answer, for the decisions a command
    makes itself after its settings are resolved (a confirmation of its own, a flow of its own). False
    outside a command `options()` was put on.
    """
    ctx = click.get_current_context(silent=True)
    # meta is one dict for a context and all it nests, so a group's subcommands see the group's answer.
    return ctx is not None and bool(ctx.meta.get(INTERACTIVE))


def options(
    settings_class,
    *,
    only=None,
    exclude=(),
    prefix=None,
    interactive=None,
    help=None,
    epilog=None,
    current=None,
    name="settings",
    errors=ErrorStyle.USAGE,
):
    """
    Puts `settings_class`'s settings on the click command below it, and hands the resolved instance to the
    command as `settings` (`name=`). Every keyword has a default:

    - `only` / `exclude` - the top-level settings this command takes, when it takes a subset.
    - `prefix` - the environment variables' prefix, instead of the class's.
    - `interactive` - when to ask: `terminal_unless_no_input()`, `no_option_given(...)`, or a function.
    - `help` - a `HelpFormat`. `epilog(selection)` - the help's closing text for the chosen groups.
    - `current()` - `{variable: text}` the values already in place, asked with as each question's default.
    - `errors` - `ErrorStyle.USAGE`, click's usage error, or `ErrorStyle.MESSAGE`, the message alone.
    """
    binding = Binding(
        settings_class,
        only=only,
        exclude=exclude,
        prefix=prefix,
        interactive=interactive,
        help=help,
        epilog=epilog,
        current=current,
        name=name,
        errors=errors,
    )

    def decorate(command):
        if not isinstance(command, click.Command):
            raise TypeError(_("@options(...) goes above @click.command(), so it has the command to add to."))
        binding.base_params = list(command.params)
        taken = [name for param in command.params for name in [*param.opts, *param.secondary_opts]]
        if binding.interactive.adds_no_input:
            taken += ["--no-input", binding.prefix + "NO_INPUT"]
        refuse_clashes(
            clashes(settings_class, env_prefix=binding.prefix, taken=["--help", *taken]),
            command.name,
        )
        command.__class__ = _command_class(type(command))
        command.isik_binding = binding
        command.params = [*binding.base_params, *binding.params(default_selection(settings_class))]
        command.callback = functools.update_wrapper(
            functools.partial(_run, binding, command.callback), command.callback
        )
        return command

    return decorate
