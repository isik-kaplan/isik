# settings

`isik.common.settings` - settings declared once, as class attributes, and read from environment
variables, from code, or (through [isik.cli](../../cli/README.md)) from options and questions. No
dependencies.

```python
from pathlib import Path

from isik.common.settings import UNSET, Setting, Settings


class Backup(Settings, prefix="BACKUP_"):
    target = Setting(Path, "Where to write the backup", default=lambda: Path.home() / "backups")
    level = Setting(int, "Compression level", default=3, min=1, max=22, soft_max=19)
    encrypt = Setting(bool, "Encrypt the backup?", default=False)
    password = Setting(str, "Encryption password", secret=True, default="", only_when=lambda values: values.encrypt)
    keep = Setting(int, "Backups to keep", default=UNSET, shown="the server's")

    def check(self):
        if self.target.is_file():
            raise self.error("target", "must be a directory, not a file")


backup = Backup.from_env()               # BACKUP_TARGET, BACKUP_LEVEL, ... - and BACKUP_TARGET_FILE
backup = Backup(level=5)                 # in code
backup.source("level")                   # Source.CODE, Source.ENV, Source.QUESTION, ...
backup.replace(level=6)                  # or dataclasses.replace(backup, level=6)
```

- **A `Settings` subclass is a frozen dataclass**: instances compare and hash by value, and
  `dataclasses.replace`/`fields` work on them. Fields are keyword-only.
- **Precedence**: option, then environment variable, then the answer to a question, then the current
  value (`current=`), then the default. Each value remembers where it came from: `source(name)`, and
  `sources(name)` for each item of a multiple setting.
- **`from_env(environ=None, *, prefix=None, current=None)`** reads `os.environ` (or `environ`). `current`
  is a further `{variable: text}` layer, such as what an installed service's env file already holds.
- **`check()`** runs whenever an instance is made, whether from code, `from_env()` or a command. Raise
  `self.error("name", "...")` to name the setting the way it was given (`--target ...` or
  `BACKUP_TARGET ...`), or `self.error("...")` for a whole message. A subclass that defines
  `__post_init__` calls `super().__post_init__()` to keep it.
- **`as_dict()`** gives every value in one flat dict, groups' and constants' included and unset ones
  left out.

## `Setting(kind, help, **keywords)`

`kind` is `str`, `int`, `float`, `bool`, `Path`, an `Enum`, a `str -> value` caster (`str.title`, one of
isik's casters), or a click `ParamType`. `help` is the help text, and the question unless `question=`
says otherwise. Both are required: a setting with nothing to say for itself can't be asked for. Everything else is inferred, and every keyword overrides it:

| Keyword | Default | |
| --- | --- | --- |
| `default` | none: required | A value, or a function called when the default is needed. `UNSET`: absent stays absent. |
| `required` | `False` | No default, and an empty value is refused. |
| `shown` | the default | What help and docs show as the default, e.g. `"the server's"`. |
| `secret` | `False` | Hidden when asked, never shown as a default, kept out of `repr` and of errors. |
| `confirm` | `False` | A secret is asked twice. |
| `multiple` | `False` | Many values: a repeatable option, and the variable split on `separator`. |
| `separator` | `","` | `LINES` for one value per line, for values that may hold a comma. Items are stripped and empty ones dropped. |
| `bare` | none | The value of the option given with no value (`--lock` alone). Combines with `multiple`. |
| `choices` | the Enum's members | A list, or `{label: value}`, e.g. `{"Generate a keypair": False, "Only authorize": True}` for a `bool`. |
| `min`, `max` | none | Hard limits: an answer is asked again, a given value is an error. |
| `soft_max`, `soft_override` | none | A soft limit: a given value needs the override setting; answers are confirmed once, at the end. |
| `option` | `--name` | Names (`"--register"`, `("-r", "--register")`), or `NoOption(reason=...)`. |
| `option_file` | the class's, off | Also `--name-file FILE`. |
| `env` | `PREFIX_NAME` | A full name (`"GITHUB_TOKEN"`), or `NoEnv(reason=...)`. |
| `env_file` | `True` | Also read `PREFIX_NAME_FILE`. Setting both is an error. |
| `negatable` | `True` | A `bool` is `--x/--no-x`; `False` makes it a plain `--x` flag. |
| `metavar` | the type's | `USER@HOST`. |
| `question` | `help` | The question's text, or `NoQuestion`, `AskedAtUse` or `ReplacedByFlow(flow=..., ...)`, each with `reason=`. |
| `prompt` | the class's | `Prompt.CLICK` or `Prompt.QUESTIONARY`, for this question alone. |
| `ask_when` | always | `lambda values: values.lock` - asked only when that holds of the answers so far. |
| `only_when` | always | The setting exists only when it holds; given anyway, it's an error. `OnlyWhen(test, message=...)` words it. |
| `requires`, `excludes` | none | Names, or `Requires(name, message=...)` / `Excludes(name, message=...)`: with a value, the other needs one / can't be given. |
| `on_empty` | the default | What an empty answer means: `KEEP_CURRENT`, a function that makes a value, or a value. |
| `before_ask` | none | `before_ask(default)` runs before the question, e.g. to say a file already exists. |
| `show_value` | the class's | Whether an error shows the value that was refused. Never for a secret. |

## Class keywords

`class Backup(Settings, prefix="BACKUP_", ...)` - a subclass keeps what its base said:

- `prefix` - the variables' prefix.
- `env=NoEnv(reason=...)` - no variables for any setting that doesn't name its own, in this class and in
  every group it nests, chosen ones included, unless a nested class has a `NoEnv` of its own.
- `option_file` - whether settings get `--name-file` options. Off.
- `prompt` - `Prompt.CLICK` or `Prompt.QUESTIONARY` for every question. Unset, a choice is asked with
  questionary's arrow keys and everything else with click's line prompts.
- `show_values` - whether errors show refused values. On; never for a secret.
- `messages={Message.REQUIRED: "%(name)s is needed", ...}` - every message, worded the class's way.
- `soft_override` - the setting that allows values over a soft limit, for every setting that doesn't name one.
- `ask_order` - names asked before the rest, which follow in declaration order. Help keeps declaration order.

## Groups, selectors and constants

```python
class ScryptSettings(Settings):
    cost = Setting(int, "scrypt cost (N)", default=2**14, soft_max=2**18)


class Generate(Settings, prefix="KEYGEN_"):
    kdf = Selector("KDF backend", {"scrypt": ScryptSettings, "argon2id": Argon2Settings}, default="argon2id")
    seed = Setting(str, "Seed passphrase", secret=True, confirm=True)
    kdf_settings = kdf.settings              # the chosen backend's settings, asked and shown here
    lock = Group(LockSettings, prefix="LOCK_")    # always there: --lock-expires, KEYGEN_LOCK_EXPIRES
    hash_len = Constant(32, reason="the derivation always makes a 32-byte seed for an ed25519 key")
```

- **`Selector(help, {choice: SettingsClass}, default=...)`** picks which group of settings applies.
  `selector.settings` is placed where that group's settings go. It has to come after the selector,
  both as declared and in `ask_order`, so the choice is known when the group is reached. A selector is a
  setting itself, with its choices as labels: an Enum member's value, or the key.
- **`Group(SettingsClass, prefix="")`** nests another class's settings: its variables and options take the
  prefix (`LOCK_` gives `APP_LOCK_EXPIRES` and `--lock-expires`).
- **`Group.from_dataclass(cls, prefix="", describe=None)`** does the same over a plain dataclass, for code
  that can't depend on isik, such as a plugin. The values come back as an instance of that dataclass.
  Field `metadata` gives `help`, `secret` and `shown`, or `describe(field)` returns `Setting` keywords.
  Selectors take these as choices too.
- **`Constant(value, reason=...)`** - fixed on purpose: neither given nor asked, and in `as_dict()`.

## Opt-outs

Each is an [`Exemption`](../utils/exemptions.md), listed with the rest by rule. Its reason must be at
least 40 characters, so `NoEnv(reason="fixed")` is refused: say what would go wrong, e.g. "a stray variable
would silently derive a different key".

| Exemption | Rule | |
| --- | --- | --- |
| `NoOption` | `settings.option` | No command-line option. |
| `NoEnv` | `settings.environment-variable` | No environment variable - say, where a stray one would quietly change a result. |
| `NoQuestion` | `settings.question` | Never asked. |
| `AskedAtUse` | `settings.question.asked-at-use` | Asked by the app where it's used, such as an overwrite confirmation. |
| `ReplacedByFlow` | `settings.question.replaced-by-flow` | A hand-written flow asks for it instead. `flow=` names it; isik never calls it. |
| `Constant` | `settings.constant` | Fixed. |

## Names never clash

Two settings that would share a name are refused when the class is defined, in any combination of its
selectors. The `--no-x` and `--x-file` forms and the `_FILE` variable count too: a `lock` setting's
`APP_LOCK_FILE` and a `LOCK_` group's `file` setting can't both exist. `options()` checks the same against
the command's own options, `--no-input`, and its `prefix=`.

## Opting out without a reason

Where a project makes a settings rule `Requirement.OPTIONAL` (see
[exemptions](../utils/exemptions.md#how-much-a-rule-asks)), `False` stands for the exemption:

```python
ISIK_EXEMPTIONS = {"settings.question": Requirement.OPTIONAL, "settings.environment-variable": Requirement.OPTIONAL}

verbose = Setting(bool, "Show every file", default=False, question=False)   # never asked
class Server(Settings, env=False): ...                                     # no variables at all
```

Under a strict rule, `False` is refused, naming the rule to loosen. `Constant(32)` without a reason works
the same way, under `settings.constant`.

## Errors

`SettingsError` (a [`ConfigError`](../config/exceptions.md)) names the setting the way it was given, such as
`--port`, `APP_PORT` or `APP_PORT_FILE`. It shows the refused value unless the setting is secret or
`show_value`/`show_values` says not to: `APP_SEE: expected one of none, own, all, got 'most'`.
`SettingsAborted` is a declined soft-limit confirmation.
