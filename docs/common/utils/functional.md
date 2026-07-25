# functional

Small function/decorator building blocks - stand-ins for missing callables, argument-validation for functions with mutually-exclusive kwargs, and decorators that need to avoid mutating the original function.

## noop / identity

```python
from isik.common.utils.functional import noop, identity

callback = noop        # accepts anything, returns None
transform = identity   # returns its input unchanged
```

## returns / raises

Build a callable that ignores its arguments and always returns a value, or always raises an exception - handy as a default callback.

```python
from isik.common.utils.functional import returns, raises

on_missing = returns(0)
on_invalid = raises(ValueError("bad input"))
```

## with_attrs

Decorator that stamps attributes onto the function object itself.

```python
from isik.common.utils.functional import with_attrs

@with_attrs(cache_key="widget_count")
def count_widgets():
    ...

count_widgets.cache_key  # "widget_count"
```

## require_exclusive_keys

Decorator that enforces the decorated function's keyword arguments match exactly one declared group (or none, with `allow_empty=True`) - for functions that support several mutually exclusive calling conventions.

```python
from isik.common.utils.functional import require_exclusive_keys

@require_exclusive_keys({"by_url": ["url"]}, {"by_host": ["host", "port"]})
def connect(url=None, host=None, port=None):
    ...

connect(url="https://example.com")          # OK
connect(host="localhost", port=8080)         # OK
connect(url="...", host="localhost")         # raises ValueError
```

- Keys not mentioned in any group are ignored by validation, not forbidden.
- Raises `ValueError` at decoration time if no conditions are given at all.

## cloned

Wraps `f` in a fresh function object that delegates to it, leaving `f` itself untouched - needed when stacking mutating decorators (like `with_attrs`) to produce independent variants of the same base function.

```python
from isik.common.utils.functional import cloned, with_attrs

def original(x):
    return x

foo = with_attrs(tag="foo")(cloned(original))
bar = with_attrs(tag="bar")(cloned(original))
# foo.tag == "foo", bar.tag == "bar", original has no `tag` attribute
```

## enabled_if

Decorator that replaces the function with one that always returns `if_not_enabled_return_value` when `condition` is falsy; leaves it unchanged (but cloned) when truthy. `condition` may be a callable, evaluated once at decoration time.

```python
from isik.common.utils.functional import enabled_if

@enabled_if(settings.FEATURE_ENABLED, if_not_enabled_return_value=None)
def do_the_thing():
    ...
```

- Both branches return a clone (see `cloned`), so decorators stacked above `enabled_if` never mutate the original function.
