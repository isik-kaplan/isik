# suppression

`suppress_to_sentry` and `suppress_callable_to_sentry` are `SuppressAndRun`/`suppress_callable` (see `isik.common.utils.error_handling`) pre-bound with `func=sentry_sdk.capture_exception` - use them when the "do something with the suppressed exception" is always "report it to Sentry".

```python
from isik.sentry.utils.suppression import suppress_callable_to_sentry, suppress_to_sentry

with suppress_to_sentry(ValueError):
    raise ValueError("oops")  # suppressed, reported to Sentry

@suppress_callable_to_sentry(ValueError, return_value=None)
def parse(x):
    return int(x)
```

- Both are `functools.partial` objects, so all other kwargs of the underlying helpers (`return_value`, `return_func`, extra exception types, ...) still work.
