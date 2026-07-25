# concurrency

Named factories for `threading.local`, `threading.Lock`, and `ContextVar`-backed namespaces. Reach for these when several unrelated modules need to share the same thread-local/lock/context-var without passing an instance around - they key off a string name instead.

## ThreadLocal

`ThreadLocal("FOO")` always returns the same `threading.local()` instance for that name, process-wide.

```python
from isik.common.utils.concurrency import ThreadLocal

request_local = ThreadLocal("REQUEST")
request_local.user = current_user
```

## ThreadLock

`ThreadLock("FOO")` always returns the same `threading.Lock()` instance for that name.

```python
from isik.common.utils.concurrency import ThreadLock

with ThreadLock("DB_WRITE"):
    write_to_db()
```

## ContextLocal

`ContextLocal("FOO")` always returns the same instance for that name, backed by per-key `ContextVar`s - safe across `async`/coroutine boundaries where `threading.local` isn't.

```python
from isik.common.utils.concurrency import ContextLocal

ctx = ContextLocal("REQUEST")
token = ctx.set("request_id", "abc123")
ctx.get("request_id")            # "abc123"
ctx.get("missing_key", "n/a")    # "n/a" - dict.get-style default
ctx.reset("request_id", token)
```

- All three registries are global module-level dicts keyed by name - picking the same name anywhere in the process gets you the same underlying object, there's no per-class or per-instance scoping.
