from contextlib import ContextDecorator, suppress
from functools import wraps

from isik.common.utils.functional import require_exclusive_keys


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
