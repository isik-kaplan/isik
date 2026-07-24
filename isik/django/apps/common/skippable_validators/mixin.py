from django.db.models.signals import class_prepared

from isik.django.apps.common.skippable_validators.context import (
    SkipFieldValidators,
    SkipNamedValidators,
    make_skippable,
)


class SkippableValidatorsMixin:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # _meta isn't attached yet here - the actual wrapping happens on class_prepared once it is.
        class_prepared.connect(cls._wrap_field_validators, sender=cls, weak=False)

    @staticmethod
    def _wrap_field_validators(sender, **kwargs):
        for field in sender._meta.local_fields + sender._meta.local_many_to_many:
            field.validators = [
                v if getattr(v, "_is_skippable", False) else make_skippable(v, field.name) for v in field.validators
            ]

    def skip_field_validators(self, *field_names):  # NOQA
        return SkipFieldValidators(*field_names)

    def skip_named_validators(self, *names):  # NOQA
        return SkipNamedValidators(*names)
