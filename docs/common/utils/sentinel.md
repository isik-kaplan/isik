# sentinel

`Sentinel(name)` creates a unique marker object that only equals itself and is falsy - useful as a "not provided" default that's distinguishable from `None`. Sentinels are interned by name, so two calls with the same name return the same instance.

```python
from isik.common.utils.sentinel import Sentinel

MISSING = Sentinel("MISSING")

def get(d, key, default=MISSING):
    value = d.get(key, MISSING)
    if value is MISSING:
        return default
    return value

Sentinel("MISSING") is MISSING  # True - interned by name
bool(MISSING)                  # False
```

- Name collisions are process-global (a class-level `_registry` dict) - pick distinctive names to avoid two unrelated sentinels accidentally being the same object.
