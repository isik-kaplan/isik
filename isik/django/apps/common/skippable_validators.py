from contextlib import ContextDecorator
from functools import wraps

from isik.common.utils import ContextLocal


SKIPPABLE_VALIDATOR_LOCAL = ContextLocal("SKIPPABLE_VALIDATOR_LOCAL")


def make_skippable(validator, field_name, name=None):
    validator_name = name or getattr(validator, "__name__", None)

    @wraps(validator)
    def wrapper(value):
        skipped_fields = SKIPPABLE_VALIDATOR_LOCAL.get("skipped_fields", set())
        skipped_names = SKIPPABLE_VALIDATOR_LOCAL.get("skipped_names", set())
        if not (field_name in skipped_fields or validator_name in skipped_names):
            return validator(value)
        return None

    wrapper._is_skippable = True
    return wrapper


class SkipFieldValidators(ContextDecorator):
    def __init__(self, *field_names):
        self.field_names = field_names

    def __enter__(self):
        current = SKIPPABLE_VALIDATOR_LOCAL.get("skipped_fields", set())
        self._token = SKIPPABLE_VALIDATOR_LOCAL.set("skipped_fields", current | set(self.field_names))
        return self

    def __exit__(self, *args):
        SKIPPABLE_VALIDATOR_LOCAL.reset("skipped_fields", self._token)
        return False


class SkipNamedValidators(ContextDecorator):
    def __init__(self, *names):
        self.names = names

    def __enter__(self):
        current = SKIPPABLE_VALIDATOR_LOCAL.get("skipped_names", set())
        self._token = SKIPPABLE_VALIDATOR_LOCAL.set("skipped_names", current | set(self.names))
        return self

    def __exit__(self, *args):
        SKIPPABLE_VALIDATOR_LOCAL.reset("skipped_names", self._token)
        return False


class SkippableValidatorsMixin:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for field in cls._meta.local_fields + cls._meta.local_many_to_many:
            field.validators = [
                v if getattr(v, "_is_skippable", False) else make_skippable(v, field.name) for v in field.validators
            ]

    def skip_field_validators(self, *field_names):  # NOQA
        return SkipFieldValidators(*field_names)

    def skip_named_validators(self, *names):  # NOQA
        return SkipNamedValidators(*names)
