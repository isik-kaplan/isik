from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from rest_framework import status
from rest_framework.exceptions import APIException, ErrorDetail
from rest_framework.exceptions import ValidationError as DRFValidationError

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


def coded(detail):
    """
    `detail`, a DRF ValidationError's, with each message `{"message", "code"}` - in a list always, a message
    DRF holds alone (`ValidationError({"city": "Unknown."})`) too, so every message reads the same.
    """
    if isinstance(detail, dict):
        return {key: coded(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return [_coded_entry(each) for each in detail]
    return [_coded_entry(detail)]


def _coded_entry(entry):
    # A list's entry: a message, or - many=True's, a nested list's - errors of their own.
    return coded(entry) if isinstance(entry, (dict, list)) else {"message": str(entry), "code": entry.code}


def exception_handler(exc, context):
    """
    DRF's exception handler, with each single-message error's code beside its message - `{"detail",
    "code"}`, the shape isik's request policies and preconditions answer with - so a client tells two
    refusals apart by their code, not their wording:

        REST_FRAMEWORK = {"EXCEPTION_HANDLER": "isik.django.drf.error_handling.exception_handler"}

        403  {"detail": "You do not have permission to perform this action.", "code": "permission_denied"}
        404  {"detail": "No Widget matches the given query.", "code": "not_found"}

    A validation error raised as one message - `ValidationError({"detail": "..."}, code=...)` - is answered
    so too. Any other carries each message's code: every message in it is `{"message", "code"}`, in the place
    DRF puts the message - a field's list, `non_field_errors`, a nested or many=True serializer's:

        400  {"detail": "Disable it first.", "code": "disable_first"}
        400  {"email": [{"message": "Enter a valid email address.", "code": "invalid"}]}

    Django's own ValidationError - a model's `full_clean()` refusing a row, a constraint's message - is a
    400 in that shape too (`drf_validation_error()`), not a 500. Whatever a view rendered itself is its own.
    """
    # Imported here: `rest_framework.views` imports the project's DEFAULT_PERMISSION_CLASSES while it loads,
    # and an isik permission among them loads this module - before that module has its handler to give.
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, ValidationError):
        exc = drf_validation_error(exc)
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    one = isinstance(response.data, dict) and set(response.data) == {"detail"}
    if one and isinstance(response.data["detail"], ErrorDetail):
        response.data["code"] = response.data["detail"].code
    elif isinstance(exc, DRFValidationError):
        response.data = coded(exc.detail)
    return response


class Refused(APIException):
    """
    A request refused, its body as given - `{"detail", "code"}` and any data beside them, each value as it
    is, never made a message. 409 unless it says otherwise.
    """

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, body, *, status_code=None):
        super().__init__()
        self.detail = body
        if status_code is not None:
            self.status_code = status_code


def refusal(message, *, code, status=None, **data):
    """
    `Refused`, its body `{"detail", "code"}` and `data` beside them, whatever exception handler a project
    sets - a client reads the code, and the data as it was given:

        raise refusal(_("Some organizations refused you."), code="join_refused", status=400,
                      refused=[{"organization": str(pk), "reason": "schema_is_gone"}])

        400  {"refused": [{"organization": "01a1...", "reason": "schema_is_gone"}],
              "detail": "Some organizations refused you.", "code": "join_refused"}
    """
    return Refused({**data, "detail": message, "code": code}, status_code=status)
