import pytest
from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.test import RequestFactory

from isik.django.apps.common.middleware.session import CookieORHeaderSessionMiddleware


@pytest.fixture
def middleware():
    return CookieORHeaderSessionMiddleware(get_response=lambda request: None)


@pytest.fixture
def rf():
    return RequestFactory()


def test_reads_the_session_key_from_the_cookie(middleware, rf):
    request = rf.get("/", HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}=cookie-key")
    middleware.process_request(request)
    assert request.session.session_key == "cookie-key"


def test_reads_the_session_key_from_the_header(middleware, rf):
    request = rf.get("/", headers={settings.SESSION_HEADER_NAME: "header-key"})
    middleware.process_request(request)
    assert request.session.session_key == "header-key"


def test_agreeing_cookie_and_header_are_both_accepted(middleware, rf):
    request = rf.get(
        "/",
        HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}=same-key",
        headers={settings.SESSION_HEADER_NAME: "same-key"},
    )
    middleware.process_request(request)
    assert request.session.session_key == "same-key"


def test_disagreeing_cookie_and_header_raise_suspicious_operation(middleware, rf):
    request = rf.get(
        "/",
        HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}=cookie-key",
        headers={settings.SESSION_HEADER_NAME: "header-key"},
    )
    with pytest.raises(SuspiciousOperation):
        middleware.process_request(request)


def test_no_cookie_or_header_starts_a_new_anonymous_session(middleware, rf):
    request = rf.get("/")
    middleware.process_request(request)
    assert request.session.session_key is None
