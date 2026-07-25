# casters

Casters used as leaf values in a `config()` schema (see `builder.py`) - each parses the raw environment-variable string into a typed value, and optionally supplies fallbacks.

## caster

Turns a plain `str -> value` function into a caster factory. The factory accepts `missing_default` (used when the env var isn't set) and `error_default` (used when parsing raises) - neither is set by default, so a missing or unparseable value raises `ConfigError`.

```python
from isik.common.config.casters import caster

@caster
def upper(value):
    return value.upper()

upper()                          # required, raises ConfigError if env var absent/unparseable
upper(missing_default="N/A")     # used if the env var is unset
upper(error_default="INVALID")   # used if upper(raw_value) raises
```

## Built-in casters

`string`, `integer`, `boolean`, `comma_separated_list`, `comma_separated_int_list`, `comma_separated_float_list` - all built with `@caster`, so all accept the same `missing_default`/`error_default` kwargs.

```python
from isik.common.config.casters import boolean, comma_separated_int_list, integer, string

string()                              # str(value)
integer()                             # int(value)
boolean()                             # "true"/"True"/"1" -> True, "false"/"False"/"0" -> False, else raises
comma_separated_int_list()            # "1,2,3" -> [1, 2, 3]
```

- `boolean` only recognizes the exact literals `"true"`, `"True"`, `"1"` (truthy) and `"false"`, `"False"`, `"0"` (falsy); anything else raises `ValueError`.
