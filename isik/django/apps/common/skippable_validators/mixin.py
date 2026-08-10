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
        weak = False  # pragma: no mutate
        # The connected function is SkippableValidatorsMixin._wrap_field_validators itself, a
        # module-level staticmethod that lives in the class's own __dict__ for the life of the
        # process - a weak reference to it would never actually go dead, so weak=True/None/omitted
        # is unobservable here (still worth being explicit, since a receiver silently dying under
        # weak=True is the usual footgun this guards against - just not one reachable through this
        # particular, permanently-referenced function).
        class_prepared.connect(cls._wrap_field_validators, sender=cls, weak=weak)  # pragma: no mutate

    @staticmethod
    def _wrap_field_validators(sender, **kwargs):
        for field in sender._meta.local_fields + sender._meta.local_many_to_many:
            field.validators = [v if _is_skippable(v) else make_skippable(v, field.name) for v in field.validators]

    def skip_field_validators(self, *field_names):  # NOQA
        return SkipFieldValidators(*field_names)

    def skip_named_validators(self, *names):  # NOQA
        return SkipNamedValidators(*names)


def _is_skippable(v):
    return getattr(v, "_is_skippable", False)  # pragma: no mutate
    # Only used as a truthy check - None and False are both falsy and select the same branch, so
    # this default's exact value is unobservable.
