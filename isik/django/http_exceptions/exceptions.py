from http import HTTPStatus

from django.http import HttpResponse, JsonResponse

from isik.common.utils.metaclasses import is_dunder, transform


class HTTPException(Exception):
    _error_handlers = []

    @classmethod
    def with_response(cls, response):
        exception = cls()
        response.status_code = exception.status
        exception.response = response
        return exception

    @classmethod
    def with_content(cls, content):
        exception = cls()
        exception.response = HttpResponse(content, status=exception.status)
        return exception

    @classmethod
    def with_json(cls, json_data):
        exception = cls()
        exception.response = JsonResponse(json_data, status=exception.status)
        return exception

    @classmethod
    def register_default_view(cls, view):
        cls._default_view = staticmethod(view)

    @classmethod
    def register_error_handler(cls, handler):
        cls._error_handlers.append(handler)
        return handler

    @classmethod
    def remove_error_handler(cls, handler):
        cls._error_handlers.remove(handler)

    @classmethod
    def _has_default_view(cls):
        return hasattr(cls, "_default_view")

    @classmethod
    def _get_default_view_response(cls, request):
        response = cls._default_view(request)
        response.status_code = cls.status
        return response

    def __call__(self, *args):
        self.args += args
        return self


class HTTPExceptions(metaclass=transform):
    """
    One raisable HTTPException subclass per member of http.HTTPStatus, built from the
    HTTPStatus members themselves via the transform metaclass - HTTPExceptions.NOT_FOUND,
    HTTPExceptions.FORBIDDEN, and so on.
    """

    __abstract__ = True
    BASE_EXCEPTION = HTTPException
    encapsulated = ["exceptions", "register_base_exception"]
    exceptions = []

    def __transform__(key, value, classdict):
        base_exception = classdict.get("BASE_EXCEPTION") or HTTPException
        if not issubclass(base_exception, HTTPException):
            raise TypeError("BASE_EXCEPTION must be a subclass of HTTPException.")
        return type(
            value.name,
            (base_exception,),
            {"__module__": __name__, "status": value.value, "description": value.description},
        )

    def __checks__(key, value, classdict):
        return all(
            (
                not is_dunder(key),
                not callable(value),
                not isinstance(value, Exception),
                not isinstance(value, classmethod),
                key != "encapsulated",
                key not in classdict.get("encapsulated", []),
            )
        )

    @classmethod
    def from_status(cls, code):
        """Get the exception class for an HTTP status code, e.g. from_status(404)."""
        return getattr(cls, HTTPStatus(code).name)

    @classmethod
    def register_base_exception(cls, new_exception):
        for exception in cls.exceptions:
            if not issubclass(new_exception, HTTPException):
                raise TypeError("New exception must be a subclass of HTTPException.")
            getattr(cls, exception).__bases__ = (new_exception,)

    for status in HTTPStatus:
        locals()[status.name] = status
        exceptions.append(status.name)
    del status
