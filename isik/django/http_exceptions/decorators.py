import functools

from django.utils.decorators import method_decorator


def errorify(error):
    """
    Decorator that makes a view - function or class-based - always raise `error` with the
    view's own return value attached as its response, instead of returning it normally.
    """

    def decorator(view):
        if isinstance(view, type):
            return _errorify_class(view, error)
        return _errorify_function(view, error)

    return decorator


def _errorify_class(cls, error):
    return method_decorator(functools.partial(_errorify_function, error=error), name="dispatch")(cls)


def _errorify_function(f, error):
    @functools.wraps(f)
    def inner(*a, **kw):
        raise error.with_response(f(*a, **kw))

    return inner
