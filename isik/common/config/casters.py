from functools import wraps


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


@caster
def comma_separated_list(value):
    return value.split(",")


@caster
def comma_separated_int_list(value):
    return [int(i) for i in value.split(",")]


@caster
def comma_separated_float_list(value):
    return [float(i) for i in value.split(",")]


@caster
def boolean(value):
    truthy = ["true", "True", "1"]
    falsy = ["false", "False", "0"]

    if value in truthy:
        return True
    elif value in falsy:
        return False
    else:
        raise ValueError(f"Value {value!r} can not be parsed into a boolean.")


string = caster(str)
integer = caster(int)
