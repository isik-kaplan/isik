# builder

`config(schema, *, prefix=None, sep="__")` builds a `Config` (a `dict` subclass with attribute access) from environment variables, using a nested dict of casters as the schema. Reach for this instead of scattering `os.environ[...]` calls when you want typed, validated settings loaded once at import time.

```python
from isik.common.config import config, string, integer, boolean

# env: NAME=Widget, DATABASE__HOST=localhost, DATABASE__PORT=5432
settings = config({
    "NAME": string(),
    "DEBUG": boolean(missing_default=False),
    "DATABASE": {
        "HOST": string(),
        "PORT": integer(),
    },
}, prefix=None, sep="__")

settings.NAME              # "Widget"
settings["NAME"]           # same, item access also works
settings.DATABASE.HOST     # "localhost"

settings.refresh()                    # re-read every value from the environment, in place
settings.DATABASE.refresh("HOST")     # re-read just one nested leaf
```

- Nested keys are joined with `sep` (and `prefix`, if given) to form the environment variable name, e.g. `DATABASE__HOST`.
- Every schema value must be either a caster (a callable produced by `@caster`, see `casters.py`) or another nested dict - anything else raises `ConfigError` at build time.
- A schema key that collides with a `dict` method name (`items`, `keys`, ...) or with `refresh` itself is only reachable via `config["that_key"]`, not attribute access - `__setattr__`/`__getattr__` are the only overrides, `dict`'s own methods still win.
- `refresh()` mutates the existing `Config`/nested `Config` objects in place, so references grabbed before a refresh (e.g. `db = settings.DATABASE`) see the update too.
- `config.ref(*path)` (or `config.ref(dot="DRF.PAGE_SIZE")` as shorthand for a nested path) is a `missing_default`/`error_default` that falls back to another setting in the same schema instead of a static value:

    ```python
    settings = config({
        "PAGE_SIZE": integer(missing_default=100),
        "MAX_PAGE_SIZE": integer(missing_default=config.ref("PAGE_SIZE")),
    })
    ```

    If `MAX_PAGE_SIZE`'s own environment variable isn't set, this resolves `PAGE_SIZE` (through its own environment variable and caster) and uses that instead - which can itself fall back further, chaining through any number of `ref()`s to a static default. Pointing `ref()` at an unknown key, at a nested config instead of a single setting, or at a cycle (`A` refs `B` refs `A`) all raise `ConfigError` immediately. Also importable directly as `ref`/`Ref` from `isik.common.config`.
