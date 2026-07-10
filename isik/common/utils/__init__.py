import re
import threading
from contextlib import ContextDecorator, suppress
from contextvars import ContextVar
from functools import wraps
from itertools import combinations


def noop(*a, **kw):
    """Don't do anything, returns None"""
    pass


def identity(x):
    """Returns the input as is."""
    return x


def with_attrs(**kwargs):
    """
    Creates a decorator that adds the given attributes to the decorated function.
    """

    def decorator(func):
        for key, value in kwargs.items():
            setattr(func, key, value)
        return func

    return decorator


def returns(value):
    """
    Creates a lambda function that returns the given value.
    """

    def wrapper(*a, **kw):
        return value

    return wrapper


def raises(exception):
    """
    Creates a lambda function that raises the given exception.
    """

    def wrapper(*a, **kw):
        raise exception

    return wrapper


def camel_to_snake(name):
    """
    Convert PascalCase and camelCase to snake_case.
    """
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def snake_to_pascal(name):
    """
    Convert snake_case to PascalCase.
    """
    return "".join(word.capitalize() for word in name.split("_"))


def snake_to_human(name):
    """
    Convert snake_case to Human Readable.
    """
    return name.replace("_", " ").title()


def require_exclusive_keys(*conditions, allow_empty=False):
    """
    A decorator that enforces mutually exclusive argument group constraints on a function.

    Ensures that the keyword arguments passed to the decorated function match
    **exactly one** of the provided key groups, or none of them if allow_empty is True.
    This is useful when a function supports multiple distinct calling conventions that
    are mutually exclusive — for example, accepting either ("url",) or ("host", "port")
    but never both or neither.

    Arguments not mentioned in any condition are unconstrained and ignored by
    the validation logic entirely.

    Args:
        *conditions: One or more dicts mapping a condition name (str) to a
                     collection of required keys (list/tuple of str).
                     Example: {"by_url": ["url"]}, {"by_host": ["host", "port"]}
        allow_empty: If True, passing none of the governed keys is also valid.
                     Defaults to False.

    Raises:
        ValueError: At decoration time, if no conditions are provided.
        ValueError: At call time, if the supplied keyword arguments do not
                    satisfy exactly one of the declared conditions (or none, if allow_empty).

    Returns:
        The decorated function, unchanged except for the added validation.

    Example:
        @require_exclusive_keys(
            {"by_url": ["url"]},
            {"by_host": ["host", "port"]},
        )
        def connect(url=None, host=None, port=None, db=None):
            ...

        connect(url="https://example.com")        # OK — matches "by_url"
        connect(url="https://example.com", db=1)  # OK — matches "by_url", db ignored
        connect(host="localhost", port=8080)       # OK — matches "by_host"
        connect(url="...", host="localhost")       # raises ValueError
        connect()                                  # raises ValueError

        @require_exclusive_keys(
            {"by_return_value": ["return_value"]},
            {"by_return_func": ["return_func"]},
            allow_empty=True,
        )
        def suppress_callable(*exceptions, return_value=None, return_func=None):
            ...

        suppress_callable(ValueError)                           # OK — neither provided
        suppress_callable(ValueError, return_value=0)          # OK — matches "by_return_value"
        suppress_callable(ValueError, return_func=my_func)     # OK — matches "by_return_func"
        suppress_callable(ValueError, return_value=0, return_func=my_func)  # raises ValueError
    """
    if not conditions:
        raise ValueError("At least one condition dict must be provided.")

    merged: dict[str, list[str]] = {}
    for condition in conditions:
        merged.update(condition)

    all_condition_keys = {k for keys in merged.values() for k in keys}

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            provided = {k for k, v in kwargs.items() if v is not None and k in all_condition_keys}

            matching = [name for name, keys in merged.items() if provided == set(keys)]

            if len(matching) != 1:
                if allow_empty and len(provided) == 0:
                    return func(*args, **kwargs)
                governed_provided = {k: v for k, v in kwargs.items() if k in all_condition_keys and v is not None}
                ungoverned_provided = {k: v for k, v in kwargs.items() if k not in all_condition_keys}
                raise ValueError(
                    f"Arguments to '{func.__name__}': the governed arguments {governed_provided!r} "
                    f"must match exactly one of: {merged}. "
                    f"Other arguments {ungoverned_provided!r} are unconstrained and were ignored."
                )

            return func(*args, **kwargs)

        return wrapper

    return decorator


def not_none(value):
    """Returns if the value is not None, mostly used as a predicate"""
    return value is not None


def first_of(iterable, default=None, pred=None):
    """
    Returns the first true value in the iterable.

    If no true value is found, returns default.
    If pred is not None, returns the first item for which pred(item) is true.

    Example:
        first_of([None, None, "x"])  # "x"
        first_of([a, b], default=c, pred=not_none)  # a if a is not None else b if b is not None else c
    """
    return next(filter(pred, iterable), default)


def cloned(f):
    """
    Returns a new function that delegates to f, without modifying f itself.

    Useful when applying a mutating decorator functionally to produce multiple
    independent variants — since some decorators modify the function in-place,
    wrapping f first ensures each variant gets its own copy:

        def original(x):
            return x

        foo = with_attrs(tag="foo")(cloned(original))
        bar = with_attrs(tag="bar")(cloned(original))

        # foo.tag == "foo", bar.tag == "bar", original is untouched
    """

    @wraps(f)
    def wrapper(*a, **kw):
        return f(*a, **kw)

    return wrapper


class Sentinel:
    """
    Creates a sentinel object that ever only equals to itself.
    Sentinels are unique by name — calling Sentinel("FOO") twice returns the same instance.

    Example:
        Sentinel("FOO") is Sentinel("FOO")  # True
    """

    _registry = {}

    def __new__(cls, name):
        if name in cls._registry:
            return cls._registry[name]
        instance = super().__new__(cls)
        cls._registry[name] = instance
        return instance

    def __init__(self, name):
        """
        Name should preferably follow the constant name convention like `SENTINEL_NAME`.
        """
        self.name = name

    def __repr__(self):
        return f"<Sentinel:{self.name}>"

    def __str__(self):
        return f"<Sentinel:{self.name}>"

    def __bool__(self):
        return False

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)


def purge_iterable(iterable, items):
    """
    Remove items from an iterable
    """
    return [item for item in iterable if item not in set(items)]


def purge_mapping(mapping, keys):
    """
    Remove keys from a mapping
    """
    return {key: value for key, value in mapping.items() if key not in keys}


def all_combinations(options):
    """
    Returns all possible combinations of the given options
    """
    return [list(comb) for r in range(1, len(options) + 1) for comb in combinations(options, r)]


class TransformExceptions(ContextDecorator):
    """
    A context manager and decorator that catches the given exception types and transforms
    them into a new exception via a transformation function.

    Args:
        *exception_types: One or more exception types to catch and transform.
        transform: A callable that receives the original exception and returns a new one.
                   This argument is required.
        keep_original: If True (default), the original exception is chained to the new one
                       via `raise new from original`, preserving the traceback context.
                       If False, the original exception is suppressed and the new one is
                       raised in isolation.

    Raises:
        Whatever exception `transform` returns: at call time if a matching exception is caught.

    Example:
        @TransformExceptions(ValueError, transform=lambda e: MyCustomError(str(e)))
        def parse(x):
            ...

        with TransformExceptions(ValueError, KeyError, transform=lambda e: MyCustomError(str(e))):
            ...

        # Without chaining:
        with TransformExceptions(ValueError, transform=lambda e: MyCustomError(str(e)), keep_original=False):
            ...
    """

    def __init__(self, *exception_types, transform, keep_original=True):
        self.exception_types = exception_types
        self.transform = transform
        self.keep_original = keep_original

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(exc_val, self.exception_types):
            new_exception = self.transform(exc_val)
            raise new_exception from (exc_val if self.keep_original else None)
        return False


class SuppressAndRun(suppress):
    """
    A context manager that suppresses the given exceptions and calls a function with the
    suppressed exception.

    Extends contextlib.suppress with the ability to run a callable when an exception is
    suppressed, for example to log it, print it, or send it to an error tracker.

    Args:
        *exceptions: One or more exception types to suppress.
        func: A callable that receives the suppressed exception as its only argument.
              Defaults to print. Must accept a single exception argument.

    Example:
        with SuppressAndRun(ValueError, func=logger.warning):
            raise ValueError("oops")  # suppressed, logger.warning called with the exception

        with SuppressAndRun(ValueError, KeyError, func=my_handler):
            ...
    """

    def __init__(self, *exceptions, func=print):
        super().__init__(*exceptions)
        self.func = func

    def __exit__(self, exc_type, exc_val, exc_tb):
        suppressed = super().__exit__(exc_type, exc_val, exc_tb)
        if suppressed:
            self.func(exc_val)
        return suppressed


@require_exclusive_keys(
    {"by_return_value": ["return_value"]},
    {"by_return_func": ["return_func"]},
    allow_empty=True,
)
def suppress_callable(*exceptions, func=print, return_value=None, return_func=None):
    """
    A decorator that suppresses the given exceptions, calls func with the suppressed
    exception, and returns either a static value or the result of a replacement function.

    Args:
        *exceptions: One or more exception types to suppress.
        func: A callable that receives the suppressed exception as its only argument.
              Defaults to print.
        return_value: A static value to return when an exception is suppressed.
                      Mutually exclusive with return_func.
        return_func: A callable with the same signature as the decorated function.
                     Called with the original arguments when an exception is suppressed.
                     Mutually exclusive with return_value.

    Raises:
        ValueError: If both return_value and return_func are provided.

    Example:
        @suppress_callable(ValueError, func=logger.warning, return_value=0)
        def parse(x):
            ...

        @suppress_callable(ValueError, return_func=lambda x: x * 0)
        def parse(x):
            ...

        @suppress_callable(ValueError)
        def parse(x):
            ...  # returns None when suppressed
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            with SuppressAndRun(*exceptions, func=func):
                return f(*a, **kw)
            if return_func is not None:
                return return_func(*a, **kw)
            return return_value

        return wrapper

    return decorator


class ThreadLocal:
    """
    A factory for thread local identified by name.
    Calling ThreadLocal("FOO") returns a thread local object shared across all calls with the same name.
    """

    _registry = {}
    _registry_lock = threading.Lock()

    def __new__(cls, name):
        if name not in cls._registry:
            with cls._registry_lock:
                if name not in cls._registry:
                    cls._registry[name] = threading.local()
        return cls._registry[name]


class ThreadLock:
    """
    A factory for thread lock identified by name.
    Calling ThreadLock("FOO") returns a lock object shared across all calls with the same name.
    """

    _registry = {}
    _registry_lock = threading.Lock()

    def __new__(cls, name):
        if name not in cls._registry:
            with cls._registry_lock:
                if name not in cls._registry:
                    cls._registry[name] = threading.Lock()
        return cls._registry[name]


class ContextLocal:
    """
    A named, registry-backed namespace of ContextVars.
    Calling ContextLocal("FOO") returns the same instance across all calls with the same name,
    making it safe for async and coroutine contexts.

    Usage:
        local = ContextLocal("MY_NAMESPACE")
        token = local.set("foo", "bar")
        local.get("foo")  # "bar"
        local.get("foo", "default")  # "bar"
        local.reset("foo", token)
    """

    _registry = {}
    _registry_lock = threading.Lock()

    def __new__(cls, name):
        if name not in cls._registry:
            with cls._registry_lock:
                if name not in cls._registry:
                    instance = super().__new__(cls)
                    object.__setattr__(instance, "_name", name)
                    object.__setattr__(instance, "_vars", {})
                    cls._registry[name] = instance
        return cls._registry[name]

    def _get_var(self, key):
        vars_ = object.__getattribute__(self, "_vars")
        if key not in vars_:
            name = object.__getattribute__(self, "_name")
            vars_[key] = ContextVar(f"{name}.{key}")
        return vars_[key]

    def get(self, key, *args):
        """Get a value. Accepts an optional default as a second argument, like dict.get()."""
        return self._get_var(key).get(*args)

    def set(self, key, value):
        """Set a value and return a token that can be used to restore the previous state."""
        return self._get_var(key).set(value)

    def reset(self, key, token):
        """Reset a value to its state before the corresponding set() call."""
        self._get_var(key).reset(token)


def get_cached(obj, attr, factory):
    """
    Get a cached attribute from an object, computing and storing it if absent.

    Bypasses __getattribute__ via object.__getattribute__ and object.__setattr__,
    making it safe to call from within a custom __getattribute__ implementation.

    Args:
        obj: The object to cache the attribute on.
        attr: The attribute name to use as the cache key.
        factory: A zero-argument callable that computes the value on cache miss.

    Returns:
        The cached value if present, otherwise the result of calling factory().
    """
    try:
        return object.__getattribute__(obj, attr)
    except AttributeError:
        value = factory()
        object.__setattr__(obj, attr, value)
        return value


def enabled_if(condition, *, if_not_enabled_return_value):
    """
    A decorator that conditionally enables a function based on a condition.
    If the condition is falsy, the decorated function is replaced with one that
    always returns if_not_enabled_return_value. If truthy, the function is unchanged.

    Args:
        condition: A boolean or a zero-argument callable that returns a boolean.
                   Callables are evaluated once at decoration time, not per call.
        if_not_enabled_return_value: The value to return when the condition is falsy.
                                     Must be passed as a keyword argument.

    Example:
        @enabled_if(settings.FEATURE_ENABLED, if_not_enabled_return_value=None)
        def my_func():
            ...

        @enabled_if(lambda: settings.FEATURE_ENABLED, if_not_enabled_return_value=None)
        def my_func():
            ...

    Note:
        Both branches return a clone of the function rather than the original, so
        any mutating decorator stacked above this one will not affect the original function.
    """
    if not (condition() if callable(condition) else condition):
        return returns(cloned(returns(if_not_enabled_return_value)))

    return cloned