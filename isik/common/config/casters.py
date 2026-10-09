from functools import wraps

from isik._internal.translation import gettext as _


def caster(f):
    """
    Turns a plain string->value function into a config caster that accepts an optional
    missing_default (used when the environment variable isn't set) and error_default (used
    when f(value) raises). Neither is set by default, meaning a missing or unparseable
    value raises ConfigError instead.
    """
    sentinel = object()

    def wrapper(missing_default=sentinel, error_default=sentinel):
        @wraps(f)
        def function_clone(value):
            return f(value)

        if missing_default is not sentinel:
            function_clone.missing_default = missing_default
        if error_default is not sentinel:
            function_clone.error_default = error_default
        return function_clone

    return wrapper


def _comma_separated(value):
    # A variable that is set but empty (`ALLOWED_HOSTS=` in a compose file) is the empty list, not
    # one empty item. An empty item between two commas stays what it is.
    return value.split(",") if value else []


@caster
def comma_separated_list(value):
    return _comma_separated(value)


@caster
def comma_separated_int_list(value):
    return [int(i) for i in _comma_separated(value)]


@caster
def comma_separated_float_list(value):
    return [float(i) for i in _comma_separated(value)]


@caster
def boolean(value):
    truthy = ["true", "True", "1"]
    falsy = ["false", "False", "0"]

    if value in truthy:
        return True
    elif value in falsy:
        return False
    else:
        raise ValueError(_("Value %(value)r can not be parsed into a boolean.") % {"value": value})


string = caster(str)
integer = caster(int)
