import inspect
from functools import wraps


def validate_inputs(*validators, **kwarg_validators):
    """
    Validates a function's arguments where the function is declared, before its body runs, so a
    failure names the argument rather than surfacing as whatever the body did with a value it
    should never have received.

    Positional validators line up with the function's own positional parameters, left to right -
    self/cls is skipped automatically, since a method's caller never passes it explicitly. Keyword
    validators name a parameter directly, and apply to it however the caller happened to pass it -
    positionally or by keyword.

        @validate_inputs(is_positive, unit=is_supported_unit)
        def resize(value, *, unit="cm"):
            ...

        resize(10, unit="cm")   # OK
        resize(-5, unit="cm")   # raises ValueError naming "value"
        resize(10, unit="km")   # raises ValueError naming "unit"

    A validator is any callable taking the argument's value. Return a falsy value to fail with a
    plain ValueError naming the argument and its value; anything truthy passes. A validator that
    already raises its own exception (e.g. Django's Validator protocol, a domain-specific one)
    propagates that exception unchanged instead - nothing here requires a single validator
    "protocol".

    A parameter's default counts as its value too, when the caller doesn't override it - the
    function body sees it either way.

    This is for validating a plain function's/method's own arguments - not a model field
    (`SkippableValidatorsMixin`, `full_clean`) or a Django-to-DRF error translation boundary
    (`django_to_drf_validation_error`), which cover different layers entirely.

    Args:
        *validators: Callables lined up with the function's own positional parameters, left to
                     right (self/cls skipped automatically).
        **kwarg_validators: Callables keyed by parameter name.

    Raises:
        TypeError: At decoration time, if there are more positional validators than positional
                   parameters, or a keyword validator names a parameter the function doesn't have.
        ValueError: At call time, if a validator returns a falsy value - names the failing
                    argument and its value. A validator that raises its own exception propagates
                    that exception unchanged instead.

    Example:
        @validate_inputs(lambda x: x > 0, unit=lambda u: u in ("cm", "in"))
        def resize(x, *, unit="cm"):
            ...
    """

    def decorator(func):
        signature = inspect.signature(func)
        param_names = list(signature.parameters)
        skip = 1 if param_names and param_names[0] in ("self", "cls") else 0
        positional_names = [
            name
            for name, param in signature.parameters.items()
            if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ][skip:]

        if len(validators) > len(positional_names):
            raise TypeError(
                f"validate_inputs() was given {len(validators)} positional validators for "
                f"{func.__name__!r}, which only has {len(positional_names)} positional parameters "
                "(self/cls excluded)."
            )
        unknown = set(kwarg_validators) - set(param_names)
        if unknown:
            raise TypeError(
                f"validate_inputs() was given validators for parameters {func.__name__!r} "
                f"doesn't have: {sorted(unknown)}."
            )

        # strict=False (ruff B905 wants it spelled out): fewer validators than positional
        # parameters is fine, deliberately - a parameter with no validator is just unchecked, not
        # an error (already enforced the other direction, too many validators, above). Any other
        # falsy value here is equivalent - zip() only ever checks strict's truthiness.
        positional_validators = dict(zip(positional_names, validators, strict=False))  # pragma: no mutate
        all_validators = {**positional_validators, **kwarg_validators}

        @wraps(func)
        def wrapper(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            for name, validator in all_validators.items():
                value = bound.arguments[name]
                if not validator(value):
                    raise ValueError(f"{func.__name__}() got an invalid value for {name!r}: {value!r}")
            return func(*args, **kwargs)

        return wrapper

    return decorator
