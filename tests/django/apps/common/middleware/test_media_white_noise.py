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


def test_debug_true_serves_a_real_media_file(settings, rf):
    settings.DEBUG = True
    middleware = make_middleware()
    request = rf.get("/media/hello.txt")
    response = middleware(request)
    assert response.status_code == 200
    assert b"".join(response) == b"hello from media\n"


def test_debug_true_falls_through_for_an_unknown_path(settings, rf):
    settings.DEBUG = True
    middleware = make_middleware()
    request = rf.get("/media/does-not-exist.txt")
    response = middleware(request)
    assert response.content == b"fallback"


def test_media_url_falsy_falls_back_to_the_root_prefix_without_crashing(settings):
    settings.MEDIA_URL = ""
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
