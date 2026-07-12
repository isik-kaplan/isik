from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError

from isik.common.utils.error_handling import TransformExceptions


@TransformExceptions(ValidationError)
def django_to_drf_validation_error(e):
    """
    Turns a django ValidationError into a DRF one, so it comes out as a proper DRF
    Response instead of falling through to Django's error handling.

    Use as a decorator on anything that can raise django's ValidationError but is
    called from DRF-land, e.g. a serializer method that calls into model.full_clean().
    """
    detail = e.message_dict if hasattr(e, "error_dict") else {"non_field_errors": e.messages}
    return DRFValidationError(detail=detail)
