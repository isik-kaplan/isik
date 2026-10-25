# cli

`isik.cli` - a [`Settings`](../common/settings/README.md) class's options, environment variables and
questions on a [click](https://click.palletsprojects.com/) command, from the one declaration. Needs the
`cli` extra: `pip install isik[cli]` (click and questionary).

```python
import click

from isik.cli import options


@options(Backup)
@click.command()
@click.argument("source")                # your own click params stay plain click
def main(source, settings: Backup):
    settings.level, settings.source("level")
```

```
$ backup --help
  --target PATH             Where to write the backup  [default: ~/backups]
  --level INTEGER           Compression level (1-22, soft max 19)  [default: 3]
  --encrypt / --no-encrypt  Encrypt the backup?  [default: no]
  ...
$ backup ./photos           # in a terminal: asks for each setting not given by option or variable
Where to write the backup [~/backups]:
Compression level (1-22, soft max 19) [3]: 25
Error: --level must be at most 22
```

`@options(...)` goes above `@click.command()`, and hands the resolved instance to the command as
`settings`. A `SettingsError` becomes click's usage error, and a declined soft limit aborts. Its
keywords, each with a default:

- `only=[...]` / `exclude=[...]` - the top-level settings this command takes, when it takes a subset of
  a class another command also uses.
- `prefix` - the variables' prefix, instead of the class's.
- `interactive` - when to ask:
  - `terminal_unless_no_input()`, the default: in a terminal, unless `--no-input` or
    `<PREFIX>NO_INPUT` is set. It adds both.
  - `no_option_given(ignoring=[...])`: when no setting came as an option. The settings named don't
    count, and an environment variable never does.
  - any `policy(given, no_input)`, where `given` is `{setting: Source}` for what options and
    variables gave.
- `help=HelpFormat(...)` - `default_on_own_line`, `first_column_max`, and
  `default_text(setting, shown)` to word the default (`lambda setting, shown: shown and f"(default: {shown})"`).
  Click's layout is the default.
- `epilog(selection)` - the help's closing text, given `{selector: chosen}`.
- `current()` - `{variable: text}` for what's already in place, such as a service's env file. Each
  question is asked with it as the default; `on_empty=KEEP_CURRENT` keeps a secret on an empty answer.
- `name` - the keyword the instance is passed as, `"settings"`.

`is_interactive()`, called inside the command, says whether this run asked its questions: the policy's
answer. It's for decisions the command makes itself after its settings are resolved, such as a
confirmation or a flow of its own. A group's subcommands see the group's answer.

## Selectors

The options are built from the chosen groups. The selector's value is read from the arguments before
click parses them: the last occurrence wins, `--kdf=x` works, a consumed value is skipped, and so is
its variable. A value that isn't a choice leaves the default, so click refuses it properly. So
`backup --kdf scrypt --help` shows scrypt's options.

## Prompts

`Prompt.CLICK` asks with `click.prompt`/`click.confirm`, and `Prompt.QUESTIONARY` with questionary's
prompts, per setting or per class (see [settings](../common/settings/README.md)). Both are looked up
when asked, so a test can monkeypatch either, and click's work with `CliRunner(input=...)`.

## isik.cli.testing

```python
from isik.cli.testing import answers, assert_docs_current, assert_reachable, questionary_io

def test_every_setting_is_reachable():
    assert_reachable(main)


def test_the_docs_list_every_setting():
    assert_docs_current("docs/settings.md", main)


def test_encrypting_asks_for_a_password():
    with answers({"encrypt": True, "password": "pw"}) as script:
        CliRunner().invoke(main, ["./photos"])
```

- **`assert_reachable(target, plain_options=None)`** - a `Settings` class, or a command. It checks every
  combination of its selectors, and fails for a setting with no question text. Two settings sharing a
  name never get this far: the class or `options()` refuses them when made. On a command it also fails for a plain click option, which a
  person can pass but is never asked for. `plain_options={"verbose": NoQuestion(reason=...)}` excuses
  one. Every reason, here and in a `Setting`, is at least 40 characters. `unreachable()` returns the problems instead of failing.
- **`answers({name: value})`** answers questions without prompting. A group's setting is `group__name`
  when two share a name. A command under it always asks. It fails on a question it has no answer for,
  a refused answer, and an unused one. `"confirm_soft": False` declines the soft-limit check, and
  `script.soft` holds the lines it was asked to confirm.
- **`questionary_io(input=..., output=...)`** hands every questionary prompt prompt_toolkit's input and
  output, e.g. `create_pipe_input()`, for tests that type keystrokes.
- **`settings_table(target)`** is the Markdown table of every setting: one table for the class, then one
  per group and per selector choice. **`write_settings_table(path, target)`** writes it between
  `<!-- settings:settings -->` and `<!-- /settings:settings -->` (`name=` picks another block), and
  **`assert_docs_current(path, target)`** fails when the docs' copy is out of date.
