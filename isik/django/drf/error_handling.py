from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from rest_framework import status
from rest_framework.exceptions import APIException, ErrorDetail
from rest_framework.exceptions import ValidationError as DRFValidationError

from isik._internal.translation import gettext_lazy
from isik.common.utils.error_handling import TransformExceptions


def drf_validation_error(e):
    """
    The DRF ValidationError a Django one is: each field's messages with their codes, and the model's own -
    a constraint's, keyed `__all__` by Django - under DRF's `NON_FIELD_ERRORS_KEY`, as a serializer's are.
    """
    # Imported here, as exception_handler's is: rest_framework.views loads this module while it loads.
    from rest_framework.serializers import as_serializer_error
    from rest_framework.settings import api_settings

    detail = as_serializer_error(e)
    if NON_FIELD_ERRORS in detail:
        model_wide = detail.pop(NON_FIELD_ERRORS)
        detail[api_settings.NON_FIELD_ERRORS_KEY] = [*detail.get(api_settings.NON_FIELD_ERRORS_KEY, ()), *model_wide]
    return DRFValidationError(detail=detail)


@TransformExceptions(ValidationError)
def django_to_drf_validation_error(e):
    """
    Turns a django ValidationError into a DRF one (`drf_validation_error()`), so it comes out as a proper
    DRF Response instead of falling through to Django's error handling.

    isik's `exception_handler` does this for every view. Use this as a decorator on anything called from
    DRF-land that a project's own handler doesn't cover - e.g. a serializer method that calls into
    model.full_clean() under a different handler.
    """
    return drf_validation_error(e)


def exception_handler(exc, context):
    """
    DRF's exception handler, with each single-message error's code beside its message - `{"detail",
    "code"}`, the shape isik's request policies and preconditions answer with - so a client tells two
    refusals apart by their code, not their wording:

        REST_FRAMEWORK = {"EXCEPTION_HANDLER": "isik.django.drf.error_handling.exception_handler"}

        403  {"detail": "You do not have permission to perform this action.", "code": "permission_denied"}
        404  {"detail": "No Widget matches the given query.", "code": "not_found"}

    A validation error - its fields' messages - keeps DRF's shape; so does whatever a view rendered itself.
    Django's own ValidationError - a model's `full_clean()` refusing a row, a constraint's message - is a
    400 in that shape too (`drf_validation_error()`), not a 500.
    """
    # Imported here: `rest_framework.views` imports the project's DEFAULT_PERMISSION_CLASSES while it loads,
    # and an isik permission among them loads this module - before that module has its handler to give.
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, ValidationError):
        exc = drf_validation_error(exc)
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and set(response.data) == {"detail"}:
        detail = response.data["detail"]
        if isinstance(detail, ErrorDetail):
            response.data["code"] = detail.code
    return response


class Refused(APIException):
    """A change refused for what the row is, not what was sent - a forced subscription, say."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = gettext_lazy("This can't be changed.")
    default_code = "refused"


def refusal(message, *, code):
    """`Refused`, its body `{"detail", "code"}` whatever exception handler a project sets - a client reads the code."""
    return Refused({"detail": message, "code": code})
