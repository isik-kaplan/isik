import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from isik.django.http_exceptions.exceptions import HTTPException
from isik.django.http_exceptions.middleware import (
    ExceptionHandlerMiddleware,
    RequestContextMiddleware,
    get_current_request,
)


@pytest.fixture
def rf():
    return RequestFactory()


class TestRequestContextMiddleware:
    def test_get_current_request_is_none_outside_of_a_request(self):
        assert get_current_request() is None

    def test_get_current_request_returns_the_request_during_the_call(self, rf):
        seen = {}

        def get_response(request):
            seen["request"] = get_current_request()
            return HttpResponse()

        middleware = RequestContextMiddleware(get_response)
        request = rf.get("/")
        middleware(request)

        assert seen["request"] is request

    def test_restores_the_previous_value_after_the_call(self, rf):
        middleware = RequestContextMiddleware(lambda request: HttpResponse())
        middleware(rf.get("/"))
        assert get_current_request() is None

    def test_restores_the_previous_value_even_if_get_response_raises(self, rf):
        middleware = RequestContextMiddleware(lambda request: (_ for _ in ()).throw(ValueError("boom")))
        with pytest.raises(ValueError, match="boom"):
            middleware(rf.get("/"))
        assert get_current_request() is None


class _IsolatedException(HTTPException):
    """A dedicated HTTPException subclass so tests don't share _error_handlers with the rest of the suite."""

    _error_handlers = []
    status = 598
    description = "Isolated Middleware Test Exception"


class TestExceptionHandlerMiddleware:
    def test_call_passes_through_to_get_response(self, rf):
        middleware = ExceptionHandlerMiddleware(lambda request: HttpResponse("ok"))
        response = middleware(rf.get("/"))
        assert response.content == b"ok"

    def test_process_exception_ignores_non_http_exceptions(self, rf):
        middleware = ExceptionHandlerMiddleware(lambda request: HttpResponse())
        assert middleware.process_exception(rf.get("/"), ValueError("boom")) is None

    def test_process_exception_builds_a_response_from_description_and_status(self, rf):
        middleware = ExceptionHandlerMiddleware(lambda request: HttpResponse())
        response = middleware.process_exception(rf.get("/"), _IsolatedException())
        assert response.status_code == 598
        assert response.content == _IsolatedException.description.encode()

    def test_process_exception_prefers_an_attached_response(self, rf):
        middleware = ExceptionHandlerMiddleware(lambda request: HttpResponse())
        exc = _IsolatedException.with_content("custom body")
        response = middleware.process_exception(rf.get("/"), exc)
        assert response.content == b"custom body"

    def test_process_exception_uses_the_registered_default_view(self, rf):
        def default_view(request):
            return HttpResponse("from default view")

        _IsolatedException.register_default_view(default_view)
        try:
            middleware = ExceptionHandlerMiddleware(lambda request: HttpResponse())
            response = middleware.process_exception(rf.get("/"), _IsolatedException())
            assert response.content == b"from default view"
            assert response.status_code == 598
        finally:
            del _IsolatedException._default_view

    def test_process_exception_calls_registered_error_handlers(self, rf):
        seen = []

        def handler(request, exc):
            seen.append((request, exc))

        _IsolatedException.register_error_handler(handler)
        try:
            middleware = ExceptionHandlerMiddleware(lambda request: HttpResponse())
            request = rf.get("/")
            exc = _IsolatedException()
            middleware.process_exception(request, exc)
            assert seen == [(request, exc)]
        finally:
            _IsolatedException.remove_error_handler(handler)
