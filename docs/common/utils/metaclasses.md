# metaclasses

## is_dunder

`True` for names shaped like `__dunder__` (at least 5 chars, no triple-underscore edges). Used internally, but exported since it's a generally useful predicate when writing your own metaclass or `__getattr__`.

```python
from isik.common.utils.metaclasses import is_dunder

is_dunder("__init__")  # True
is_dunder("_private")  # False
```

## transform

Metaclass that runs every class-body attribute - and any attribute later assigned on the class - through two hooks the class defines on itself: `__checks__(key, value, classdict) -> bool` (default: transform everything) and `__transform__(key, value, classdict)` (default: identity). Both hooks are looked up on the class's own `__dict__` only, never inherited.

```python
from isik.common.utils.metaclasses import transform

class Doubled(metaclass=transform):
    __checks__ = staticmethod(lambda key, value, classdict: isinstance(value, int))
    __transform__ = staticmethod(lambda key, value, classdict: value * 2)

    one = 1

Doubled.one       # 2
Doubled.two = 10
Doubled.two       # 20
```

- Set `__abstract__ = True` in the class body to make that class raise `TypeError` on instantiation - for classes meant purely as a namespace/registry.
- Because the hooks aren't inherited, a subclass that wants the same behavior must redeclare `__checks__`/`__transform__` itself.
