from functools import wraps


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

        def decorator(func):
            @wraps(func)
            def wrapper(*a, **kw):
                return if_not_enabled_return_value

            return wrapper

        return decorator

    return cloned
