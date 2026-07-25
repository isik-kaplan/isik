# caching

`get_cached(obj, attr, factory)` computes and stores a value on first access, then returns the stored value on every call after. Reach for it when writing a custom `__getattribute__` and you need a cached attribute without recursing back into it.

```python
from isik.common.utils.caching import get_cached

class Circle:
    def __init__(self, radius):
        self.radius = radius

    def __getattribute__(self, name):
        if name == "area":
            return get_cached(self, "area", lambda: 3.14159 * self.radius**2)
        return object.__getattribute__(self, name)
```

- Uses `object.__getattribute__`/`object.__setattr__` directly, so it's safe to call from inside a custom `__getattribute__` override without infinite recursion.
