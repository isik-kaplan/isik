from django.db.models.signals import class_prepared

from isik.django.apps.common.skippable_validators.context import (
    SkipFieldValidators,
    SkipNamedValidators,
    make_skippable,
)


class SkippableValidatorsMixin:
    """Wraps every field validator of the model mixing it in so it can be skipped - see `_wrap_field_validators`."""

    def skip_field_validators(self, *field_names):  # NOQA
        return SkipFieldValidators(*field_names)

    def skip_named_validators(self, *names):  # NOQA
        return SkipNamedValidators(*names)


def _wrap_field_validators(sender, **kwargs):
    # _meta only exists once the class is prepared, hence doing this on class_prepared.
    if not issubclass(sender, SkippableValidatorsMixin):
        return
    for field in sender._meta.local_fields + sender._meta.local_many_to_many:
        field.validators = [v if _is_skippable(v) else make_skippable(v, field.name) for v in field.validators]


def _is_skippable(v):
    return getattr(v, "_is_skippable", False)  # pragma: no mutate


# One receiver for every model, connected once - not one per class with `sender=cls`. Django keys a
# receiver's sender by id(sender) and never removes it, so a per-class receiver outlives its class: once a
# model class is freed (one defined in a test, under isolate_apps), the next class Python allocates at the
# same address inherits it and gets its validators wrapped without ever mixing this in. It also left one
# receiver behind per model, forever.
class_prepared.connect(_wrap_field_validators, dispatch_uid="isik.skippable_validators")
