# exceptions

`ConfigError(ValueError)` is the single exception type raised by `config()`/`Config.refresh()` for a missing env var, an unparseable value, or a bad schema - catch `ConfigError` (or `ValueError`) around config loading rather than guessing at env-parsing internals.

```python
from isik.common.config import config, string
from isik.common.config.exceptions import ConfigError

try:
    settings = config({"NAME": string()})
except ConfigError as e:
    raise SystemExit(f"Bad configuration: {e}")
```
