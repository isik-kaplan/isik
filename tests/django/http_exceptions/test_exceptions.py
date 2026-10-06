from http import HTTPStatus

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isik.django.http_exceptions import exceptions
from isik.django.http_exceptions.exceptions import HTTPException, HTTPExceptions


def test_has_one_exception_class_per_http_status():
    for status in HTTPStatus:
        exception_class = getattr(HTTPExceptions, status.name)
        assert issubclass(exception_class, HTTPException)
        assert exception_class.status == status.value
        assert exception_class.description == status.description


def test_does_not_leak_the_loop_variable_as_a_class_attribute():
    assert not hasattr(HTTPExceptions, "status")


def test_cannot_be_instantiated():
    with pytest.raises(TypeError, match="can not be instantiated"):
        HTTPExceptions()


@given(st.sampled_from(list(HTTPStatus)))
def test_from_status_round_trips_for_every_status(status):
    assert HTTPExceptions.from_status(status.value) is getattr(HTTPExceptions, status.name)


class TestWithResponse:
    def test_attaches_the_response_and_forces_the_status_code(self):
        from django.http import HttpResponse

        response = HttpResponse("hello", status=200)
        exception = HTTPExceptions.GONE.with_response(response)

        assert isinstance(exception, HTTPExceptions.GONE)
        assert exception.response is response
        assert response.status_code == HTTPExceptions.GONE.status


class TestWithContent:
    def test_builds_a_response_with_the_given_content(self):
        exception = HTTPExceptions.NOT_FOUND.with_content("nothing here")
        assert exception.response.status_code == 404
        assert exception.response.content == b"nothing here"


class TestWithJson:
    def test_builds_a_json_response(self):
        exception = HTTPExceptions.BAD_REQUEST.with_json({"error": "bad"})
        assert exception.response.status_code == 400
        assert exception.response["Content-Type"] == "application/json"
        assert exception.response.content == b'{"error": "bad"}'


class _IsolatedException(HTTPException):
    """A dedicated HTTPException subclass so tests don't share _error_handlers with the rest of the suite."""

    _error_handlers = []
    status = 599
    description = "Isolated Test Exception"


class TestRegisterDefaultView:
    def test_has_no_default_view_by_default(self):
        assert _IsolatedException._has_default_view() is False

    def test_register_default_view_sets_the_view_and_forces_the_status_code(self):
        def view(request):
            from django.http import HttpResponse

            return HttpResponse("default")

        try:
            _IsolatedException.register_default_view(view)
            assert _IsolatedException._has_default_view() is True

            response = _IsolatedException._get_default_view_response(request=None)
            assert response.content == b"default"
            assert response.status_code == 599
        finally:
            # _IsolatedException is a module-level class, and register_default_view() sets a
            # class attribute with no counterpart to unset it - clean up so this test's effect
            # doesn't leak into test_has_no_default_view_by_default on a second in-process run of
            # this file (e.g. a mutation-testing tool re-invoking pytest without restarting).
            del _IsolatedException._default_view


class TestErrorHandlers:
    def test_register_and_remove_error_handler(self):
        calls = []

        def handler(request, exc):
            calls.append((request, exc))

        _IsolatedException.register_error_handler(handler)
        assert handler in _IsolatedException._error_handlers

        _IsolatedException.remove_error_handler(handler)
        assert handler not in _IsolatedException._error_handlers


def test_calling_the_exception_appends_to_its_args():
    exception = _IsolatedException("first")
    result = exception("extra", "info")
    assert result is exception
    assert exception.args == ("first", "extra", "info")


def test_transform_rejects_a_base_exception_that_is_not_an_http_exception_subclass():
    transform_hook = HTTPExceptions.__dict__["__transform__"]
    with pytest.raises(TypeError, match="must be a subclass of HTTPException"):
        transform_hook("NOT_FOUND", HTTPStatus.NOT_FOUND, {"BASE_EXCEPTION": ValueError})


class TestRegisterBaseException:
    def test_rejects_a_base_that_is_not_an_http_exception_subclass(self):
        with pytest.raises(TypeError, match=r"^New exception must be a subclass of HTTPException\.$"):
            HTTPExceptions.register_base_exception(ValueError)

    def test_reassigns_bases_for_each_registered_exception(self):
        class Foo(HTTPException):
            pass

        class Container(HTTPExceptions):
            exceptions = ["Foo"]

        Container.Foo = Foo

        class NewBase(HTTPException):
            pass

        Container.register_base_exception(NewBase)

        assert Foo.__bases__ == (NewBase,)

    def test_is_a_noop_when_no_exceptions_are_registered(self):
        class Container(HTTPExceptions):
            exceptions = []

        class NewBase(HTTPException):
            pass

        Container.register_base_exception(NewBase)


class TestTheHooksThatBuildEachException:
    """HTTPExceptions runs these while its own class is created - here, each is asked directly."""

    def test_a_status_becomes_an_exception_class_named_for_it(self):
        made = exceptions._exception_for_status("NOT_FOUND", HTTPStatus.NOT_FOUND, {})

        assert (made.__name__, made.__bases__, made.__module__) == ("NOT_FOUND", (HTTPException,), exceptions.__name__)
        assert (made.status, made.description) == (404, HTTPStatus.NOT_FOUND.description)

    def test_on_the_base_exception_the_class_names(self):
        class Base(HTTPException):
            pass

        made = exceptions._exception_for_status("GONE", HTTPStatus.GONE, {"BASE_EXCEPTION": Base})

        assert made.__bases__ == (Base,)

    def test_a_base_exception_that_is_not_an_http_exception_is_refused(self):
        with pytest.raises(TypeError, match=r"^BASE_EXCEPTION must be a subclass of HTTPException\.$"):
            exceptions._exception_for_status("GONE", HTTPStatus.GONE, {"BASE_EXCEPTION": ValueError})

    def test_a_plain_status_is_turned_into_an_exception(self):
        assert exceptions._is_a_status("GONE", HTTPStatus.GONE, {}) is True
        assert exceptions._is_a_status("GONE", HTTPStatus.GONE, {"encapsulated": ["other"]}) is True

    @pytest.mark.parametrize(
        ("key", "value", "classdict"),
        [
            ("__doc__", "a dunder", {}),
            ("helper", lambda: None, {}),
            ("raised", ValueError(), {}),
            ("built", classmethod(lambda cls: None), {}),
            ("encapsulated", ["exceptions"], {}),
            ("exceptions", [], {"encapsulated": ["exceptions"]}),
        ],
    )
    def test_everything_else_is_left_alone(self, key, value, classdict):
        assert exceptions._is_a_status(key, value, classdict) is False
