# error_handling

Context managers and decorators for turning exceptions into something else - a different exception, a log call, or a fallback return value - without hand-rolling `try`/`except` at every call site.

## TransformExceptions

Catches the given exception types and re-raises a new one produced by `transform`. Works as a context manager or a decorator, and supports a two-step form for reusing the transform itself as a named decorator.

```python
from isik.common.utils.error_handling import TransformExceptions

@TransformExceptions(ValueError, transform=lambda e: MyCustomError(str(e)))
def parse(x):
    return int(x)

# two-step form: build a reusable, named decorator
@TransformExceptions(ValueError)
def value_error_to_my_error(e):
    return MyCustomError(str(e))

@value_error_to_my_error
def parse(x):
    return int(x)
```

- `keep_original=True` (default) chains the original exception via `raise new from original`; pass `keep_original=False` to suppress it instead.

## SuppressAndRun

Extends `contextlib.suppress` to also call `func` with the suppressed exception (e.g. to log or report it).

```python
from isik.common.utils.error_handling import SuppressAndRun

with SuppressAndRun(ValueError, func=logger.warning):
    raise ValueError("oops")  # suppressed; logger.warning(exc) is called
```

## suppress_callable

Decorator form of `SuppressAndRun` that also lets the wrapped call return a fallback value when an exception is suppressed.

```python
from isik.common.utils.error_handling import suppress_callable

@suppress_callable(ValueError, func=logger.warning, return_value=0)
def parse(x):
    return int(x)

@suppress_callable(ValueError, return_func=lambda x: len(x))
def parse(x):
    return int(x)
```

- `return_value` and `return_func` are mutually exclusive (raises `ValueError` if both are given); with neither, a suppressed call returns `None`.
