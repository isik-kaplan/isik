import textwrap

from click.formatting import measure_table

from isik._internal.translation import gettext as _
from isik.common.settings.resolution import display_default


class HelpFormat:
    """
    How a command's options are laid out in `--help`. Each is off by default, keeping click's own layout:

    - `default_on_own_line` - `[default: ...]` under the description instead of after it.
    - `first_column_max` - the widest the option column may make the description column narrower.
    - `default_text(setting, shown)` - the text for a setting's default, given what isik would show (None
      when there's nothing to): return None to show none, or e.g. `f"(default: {shown})"`.
    - `default_separator` - between the description and a default on the same line, two spaces as click has.
    - `option_file_help(setting, option)` - the help of a setting's `--x-file` option.
    """

    def __init__(
        self,
        *,
        default_on_own_line=False,
        first_column_max=None,
        default_text=None,
        default_separator="  ",
        option_file_help=None,
    ):
        self.default_on_own_line = default_on_own_line
        self.first_column_max = first_column_max
        self.default_text = default_text
        self.default_separator = default_separator
        self.option_file_help = option_file_help

    def file_help(self, setting, option):
        """The help of `setting`'s option that reads it from a file - `option` is the one it reads."""
        if self.option_file_help is not None:
            return self.option_file_help(setting, option)
        return _("Read %(option)s from FILE.") % {"option": option}

    def default_line(self, setting):
        """The text help shows for `setting`'s default, or None."""
        shown = display_default(setting)
        if setting.is_bool() and not setting.negatable and setting.shown is None:
            shown = None
        if self.default_text is not None:
            return self.default_text(setting, shown)
        return None if shown is None else _("[default: %(default)s]") % {"default": shown}

    def describe(self, setting, text):
        """(help, own line) - the option's help, and the default to show under it when it goes on its own line."""
        default = self.default_line(setting)
        if not default:
            return text, None
        if self.default_on_own_line:
            return text, default
        return f"{text}{self.default_separator}{default}", None

    def format_options(self, command, ctx, formatter):
        found = [(param, record) for param in command.get_params(ctx) if (record := param.get_help_record(ctx))]
        if not found:
            return
        first_column = measure_table([record for _param, record in found])[0]
        if self.first_column_max is not None:
            first_column = min(first_column, self.first_column_max)
        text_width = max(formatter.width - first_column - 4, 10)
        rows = []
        for param, (name, help_text) in found:
            default = getattr(param, "isik_default", None)
            if default:
                # \b keeps click from rewrapping the lines laid out here.
                help_text = "\b\n" + "\n".join([*textwrap.wrap(help_text, text_width), default])
            rows.append((name, help_text))
        with formatter.section(_("Options")):
            formatter.write_dl(rows)
