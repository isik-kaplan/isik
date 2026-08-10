import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import get_script_prefix, set_script_prefix

from isik.django.apps.common.middleware.media_white_noise import MediaWhiteNoiseMiddleware


@pytest.fixture
def rf():
    return RequestFactory()


def make_middleware(get_response=None):
    return MediaWhiteNoiseMiddleware(get_response=get_response or (lambda request: HttpResponse("fallback")))


def test_static_prefix_matches_media_url(settings):
    settings.MEDIA_URL = "/media/"
    middleware = make_middleware()
    assert middleware.directories[0][1] == "/media/"


def test_debug_false_falls_through_to_get_response(settings, rf):
    settings.DEBUG = False
    middleware = make_middleware()
    request = rf.get("/media/hello.txt")
    response = middleware(request)
    assert response.content == b"fallback"


def test_debug_false_passes_the_real_request_through_not_none(settings, rf):
    settings.DEBUG = False
    middleware = make_middleware(get_response=lambda request: HttpResponse(request.path))
    response = middleware(rf.get("/some/path"))
    assert response.content == b"/some/path"


def test_debug_true_serves_a_real_media_file(settings, rf):
    settings.DEBUG = True
    middleware = make_middleware()
    request = rf.get("/media/hello.txt")
    response = middleware(request)
    assert response.status_code == 200
    assert b"".join(response) == b"hello from media\n"


def test_served_file_is_never_cached(settings, rf):
    settings.DEBUG = True
    middleware = make_middleware()
    request = rf.get("/media/hello.txt")
    response = middleware(request)
    assert response.headers["Cache-Control"] == "max-age=0, public"


def test_a_304_response_has_no_content_type_header(settings, rf):
    # A 304 correctly omits entity headers like Content-Type per HTTP - whitenoise's own
    # not-modified response never sets one, so it's only absent in the end if serve() actually
    # deletes WhiteNoiseFileResponse's own default rather than leaving it to leak through.
    settings.DEBUG = True
    middleware = make_middleware()
    etag = middleware(rf.get("/media/hello.txt")).headers["ETag"]

    response = middleware(rf.get("/media/hello.txt", HTTP_IF_NONE_MATCH=etag))

    assert response.status_code == 304
    assert "Content-Type" not in response.headers


def test_script_prefix_that_does_not_prefix_the_media_url_is_left_alone(settings, rf):
    from django.urls import get_script_prefix, set_script_prefix

    settings.MEDIA_URL = "/media/"
    previous_prefix = get_script_prefix()
    set_script_prefix("/unrelated/")
    try:
        middleware = make_middleware()
    finally:
        set_script_prefix(previous_prefix)
    assert middleware.directories[0][1] == "/media/"


def test_debug_true_falls_through_for_an_unknown_path(settings, rf):
    settings.DEBUG = True
    middleware = make_middleware()
    request = rf.get("/media/does-not-exist.txt")
    response = middleware(request)
    assert response.content == b"fallback"


def test_media_url_falsy_falls_back_to_the_root_prefix_without_crashing(settings):
    # None, not "" - django.conf.settings special-cases MEDIA_URL/STATIC_URL: reading either
    # always runs them through _add_script_prefix(), so "" can never survive the round trip (it
    # comes back "/") - only an unset/None MEDIA_URL actually reaches the `or ""` fallback below.
    settings.MEDIA_URL = None
    middleware = make_middleware()
    assert middleware.directories[0][1] == "/"


def test_script_prefix_is_stripped_from_the_static_prefix(settings):
    settings.MEDIA_URL = "/myapp/media/"
    previous_prefix = get_script_prefix()
    set_script_prefix("/myapp/")
    try:
        middleware = make_middleware()
    finally:
        set_script_prefix(previous_prefix)
    assert middleware.directories[0][1] == "/media/"
