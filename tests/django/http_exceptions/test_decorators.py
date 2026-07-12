import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.views import View

from isik.django.http_exceptions.decorators import errorify
from isik.django.http_exceptions.exceptions import HTTPExceptions


@pytest.fixture
def rf():
    return RequestFactory()


def test_errorify_on_a_function_view_raises_with_the_original_response_attached(rf):
    @errorify(HTTPExceptions.PAYMENT_REQUIRED)
    def view(request):
        return HttpResponse("subscribe to continue")

    with pytest.raises(HTTPExceptions.PAYMENT_REQUIRED) as exc_info:
        view(rf.get("/"))

    assert exc_info.value.response.content == b"subscribe to continue"
    assert exc_info.value.response.status_code == HTTPExceptions.PAYMENT_REQUIRED.status


def test_errorify_on_a_class_based_view_wraps_dispatch(rf):
    @errorify(HTTPExceptions.GONE)
    class Retired(View):
        def get(self, request):
            return HttpResponse("this endpoint is retired")

    with pytest.raises(HTTPExceptions.GONE) as exc_info:
        Retired.as_view()(rf.get("/"))

    assert exc_info.value.response.content == b"this endpoint is retired"
    assert exc_info.value.response.status_code == HTTPExceptions.GONE.status
