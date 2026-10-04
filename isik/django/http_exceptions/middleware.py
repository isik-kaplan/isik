from django.http import HttpResponse

from isik._internal.translation import translate_text
from isik.common.utils.concurrency import ContextLocal
from isik.django.apps.common.middleware.base import Middleware
from isik.django.http_exceptions.exceptions import HTTPExceptions


_CURRENT_REQUEST = ContextLocal("isik.django.http_exceptions.CURRENT_REQUEST")


def get_current_request():
    return _CURRENT_REQUEST.get("request", None)


class RequestContextMiddleware(Middleware):
    """
    Stashes the current request in a ContextLocal for the duration of the request, so code
    without direct access to it (serializers, signal handlers, ...) can still reach it via
    get_current_request().
    """

    # Wraps the whole request rather than running before and after it: the reset has to happen
    # however the view ends, raising included.
    def __call__(self, request):
        token = _CURRENT_REQUEST.set("request", request)
        try:
            return self.get_response(request)
        finally:
            _CURRENT_REQUEST.reset("request", token)


class ExceptionHandlerMiddleware(Middleware):
    """
    Turns any raised HTTPExceptions.BASE_EXCEPTION into a response - using exc.response if
    one was attached (with_response/with_content/with_json), the class's registered default
    view, or a plain response built from its description and status code.
    """

    @staticmethod
    def process_exception(request, exc):
        if isinstance(exc, HTTPExceptions.BASE_EXCEPTION):
            for handler in exc._error_handlers:
                handler(request, exc)
            response = getattr(exc, "response", None)
            if not response and exc._has_default_view():
                response = exc._get_default_view_response(request)
            if not response:
                # Built-in statuses carry stdlib's English HTTPStatus description - translatable too.
                response = HttpResponse(content=translate_text(exc.description).encode(), status=exc.status)
            return response
