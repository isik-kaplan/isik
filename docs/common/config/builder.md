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
