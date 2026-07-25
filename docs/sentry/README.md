# sentry

Importing `isik.sentry` (or anything under it) checks that `sentry_sdk` is installed and raises a clear `ImportError` pointing at `pip install isik[sentry]` if it isn't, instead of a bare `ModuleNotFoundError` from deep inside the package.

```python
import isik.sentry  # raises ImportError with an install hint if sentry-sdk is missing
```

- This check runs once at import time via `isik._internal.checks.check_extra`, which every extras-gated subpackage uses the same way - it's private, not part of the public API.

- [utils/suppression.md](utils/suppression.md) - `suppress_to_sentry`, `suppress_callable_to_sentry`, exception suppression that reports to Sentry.
