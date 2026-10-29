from django.core.exceptions import ValidationError
from rest_framework.exceptions import ErrorDetail
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


def exception_handler(exc, context):
    """
    DRF's exception handler, with each single-message error's code beside its message - `{"detail",
    "code"}`, the shape isik's request policies and preconditions answer with - so a client tells two
    refusals apart by their code, not their wording:

        REST_FRAMEWORK = {"EXCEPTION_HANDLER": "isik.django.drf.error_handling.exception_handler"}

        403  {"detail": "You do not have permission to perform this action.", "code": "permission_denied"}
        404  {"detail": "No Widget matches the given query.", "code": "not_found"}

    A validation error - its fields' messages - keeps DRF's shape; so does whatever a view rendered itself.
    """
    # Imported here: `rest_framework.views` imports the project's DEFAULT_PERMISSION_CLASSES while it loads,
    # and an isik permission among them loads this module - before that module has its handler to give.
    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and set(response.data) == {"detail"}:
        detail = response.data["detail"]
        if isinstance(detail, ErrorDetail):
            response.data["code"] = detail.code
    return response
