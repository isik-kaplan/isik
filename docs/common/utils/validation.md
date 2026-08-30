# validation

## validate_inputs

Decorator that validates a function's arguments where the function is declared, before its body runs - a failure names the argument rather than surfacing as whatever the body did with a value it should never have received.

```python
from isik.common.utils.validation import validate_inputs

@validate_inputs(is_positive, unit=is_supported_unit)
def resize(value, *, unit="cm"):
    ...

resize(10, unit="cm")   # OK
resize(-5, unit="cm")   # raises ValueError naming "value"
resize(10, unit="km")   # raises ValueError naming "unit"
```

- Positional validators line up with the function's own positional parameters, left to right - `self`/`cls` is skipped automatically (name-based: the first parameter literally named `self`/`cls`), since a method's caller never passes it explicitly.
- Keyword validators name a parameter directly, and apply to it however the caller happened to pass it - positionally or by keyword.
- A parameter with no validator at all is unchecked.
- A parameter's default counts as its value too, when the caller doesn't override it - the function body sees it either way, so a bad default fails the same way a bad explicit value would.

## What a validator is

Any callable taking the argument's value:

- Return a falsy value to fail with a plain `ValueError` naming the argument and its value.
- Return anything truthy to pass.
- Raise your own exception instead (Django's `Validator` protocol, a domain-specific exception, anything) - it propagates unchanged, not wrapped. Nothing here requires a single validator "protocol" - use whichever style already fits the validator you have.

## Scope

This is for validating a plain function's or method's own arguments - not a model field (`SkippableValidatorsMixin`, `full_clean`) or a Django-to-DRF error translation boundary (`django_to_drf_validation_error`), which cover different layers entirely.

## Decoration-time errors

`TypeError` is raised immediately (not deferred to call time) if the decorator itself is misconfigured against the function's real signature:

```python
@validate_inputs(v1, v2)          # more positional validators than positional parameters
def thing(value):
    ...

@validate_inputs(nonexistent=v1)  # names a parameter that doesn't exist
def thing(value):
    ...
```
