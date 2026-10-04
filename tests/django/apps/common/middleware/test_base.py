"""Middleware - the get_response constructor, and a __call__ around before/after."""

from django.http import HttpResponse
from django.test import RequestFactory

from isik.django.apps.common.middleware import Middleware


def view_saying(text, calls=None):
    def view(request):
        if calls is not None:
            calls.append(request)
        return HttpResponse(text)

    return view


def test_without_hooks_it_hands_the_request_on_and_the_response_back():
    calls = []
    request = RequestFactory().get("/")

    response = Middleware(view_saying("from the view", calls))(request)

    assert (response.content, calls) == (b"from the view", [request])


def test_after_sees_the_request_and_the_response_and_returns_what_is_sent():
    seen = []

    class Stamping(Middleware):
        def after(self, request, response):
            seen.append((request, response))
            stamped = HttpResponse(response.content + b" + stamped")
            return stamped

    request = RequestFactory().get("/")
    response = Stamping(view_saying("from the view"))(request)

    assert response.content == b"from the view + stamped"
    assert seen[0][0] is request
    assert seen[0][1].content == b"from the view"


def test_before_returning_a_response_answers_without_the_view():
    calls = []

    class Closed(Middleware):
        def before(self, request):
            return HttpResponse("closed", status=503)

        def after(self, request, response):
            response["X-After"] = "ran"
            return response

    response = Closed(view_saying("from the view", calls))(RequestFactory().get("/"))

    assert (response.status_code, response.content, response["X-After"]) == (503, b"closed", "ran")
    assert calls == []


def test_before_returning_nothing_carries_on():
    seen = []

    class Watching(Middleware):
        def before(self, request):
            seen.append(request)

    request = RequestFactory().get("/")

    assert Watching(view_saying("from the view"))(request).content == b"from the view"
    assert seen == [request]


def test_the_hooks_do_nothing_by_default():
    middleware = Middleware(view_saying("x"))
    request, response = RequestFactory().get("/"), HttpResponse()

    assert middleware.before(request) is None
    assert middleware.after(request, response) is response
