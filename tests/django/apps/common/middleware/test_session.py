import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
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
    with pytest.raises(SuspiciousOperation, match="^Session key mismatch$"):
        middleware.process_request(request)


def test_no_cookie_or_header_starts_a_new_anonymous_session(middleware, rf):
    request = rf.get("/")
    middleware.process_request(request)
    assert request.session.session_key is None


def test_missing_session_header_name_setting_raises_improperly_configured(settings):
    del settings.SESSION_HEADER_NAME
    with pytest.raises(
        ImproperlyConfigured, match="^CookieORHeaderSessionMiddleware requires settings.SESSION_HEADER_NAME"
    ):
        CookieORHeaderSessionMiddleware(get_response=lambda request: None)


@pytest.mark.django_db
class TestDeadSessionCookie:
    def respond(self, status, *, cookie=True, signed_in=False, key="dead"):
        from django.contrib.auth.models import AnonymousUser
        from django.http import HttpResponse

        from isik.django.apps.common.middleware.session import DeadSessionCookieMiddleware

        request = RequestFactory().get("/")
        if cookie:
            request.COOKIES[settings.SESSION_COOKIE_NAME] = key
        request.user = type("User", (), {"is_authenticated": True})() if signed_in else AnonymousUser()
        handed = []

        def respond(given):
            handed.append(given)
            return HttpResponse(status=status)

        response = DeadSessionCookieMiddleware(respond)(request)
        assert handed == [request]
        return response

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refusal_with_a_cookie_and_no_user_deletes_it_and_says_so(self, status):
        response = self.respond(status)

        morsel = response.cookies[settings.SESSION_COOKIE_NAME]
        assert morsel.value == ""
        assert morsel["max-age"] == 0
        assert (morsel["path"], morsel["domain"], morsel["samesite"]) == (
            settings.SESSION_COOKIE_PATH,
            settings.SESSION_COOKIE_DOMAIN or "",
            settings.SESSION_COOKIE_SAMESITE,
        )
        assert ("Session-Expired", "true") in response.items()

    def test_deleted_where_it_was_set(self, settings):
        settings.SESSION_COOKIE_PATH = "/app/"
        settings.SESSION_COOKIE_DOMAIN = ".example.com"

        morsel = self.respond(401).cookies[settings.SESSION_COOKIE_NAME]

        assert (morsel["path"], morsel["domain"]) == ("/app/", ".example.com")

    def test_the_header_is_a_setting(self, settings):
        settings.DEAD_SESSION_HEADER = "Sign-In-Again"

        assert self.respond(401)["Sign-In-Again"] == "true"

    @pytest.mark.parametrize(
        ("status", "cookie", "signed_in"),
        [(200, True, False), (404, True, False), (401, False, False), (403, True, True)],
    )
    def test_anything_else_is_left_alone(self, status, cookie, signed_in):
        response = self.respond(status, cookie=cookie, signed_in=signed_in)

        assert settings.SESSION_COOKIE_NAME not in response.cookies
        assert "Session-Expired" not in response

    def test_without_a_user_on_the_request_it_isnt_signed_in(self):
        from django.http import HttpResponse

        from isik.django.apps.common.middleware.session import DeadSessionCookieMiddleware

        request = RequestFactory().get("/")
        request.COOKIES[settings.SESSION_COOKIE_NAME] = "dead"

        response = DeadSessionCookieMiddleware(lambda request: HttpResponse(status=401))(request)

        assert response["Session-Expired"] == "true"


def live_session(**data):
    """A session saved in the configured store, holding `data` - a sign-in waiting for its next step, say."""
    from importlib import import_module

    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session.update(data or {"pending_login": "mfa"})
    session.save()
    return session.session_key


@pytest.mark.django_db
class TestASessionStillInTheStore:
    """A pending sign-in answers 401 while its session holds the step to come back to - its cookie stays."""

    respond = TestDeadSessionCookie.respond

    @pytest.mark.parametrize(
        "engine",
        [
            "django.contrib.sessions.backends.db",
            "django.contrib.sessions.backends.cache",
            "django.contrib.sessions.backends.cached_db",
            "django.contrib.sessions.backends.signed_cookies",
        ],
    )
    def test_keeps_its_cookie_and_a_missing_one_is_cleared(self, settings, engine):
        settings.SESSION_ENGINE = engine

        kept = self.respond(401, key=live_session())
        cleared = self.respond(401, key="no-such-session")

        assert settings.SESSION_COOKIE_NAME not in kept.cookies
        assert "Session-Expired" not in kept
        assert cleared.cookies[settings.SESSION_COOKIE_NAME].value == ""
        assert cleared["Session-Expired"] == "true"

    def test_one_flushed_since_is_dead(self, settings):
        from importlib import import_module

        settings.SESSION_ENGINE = "django.contrib.sessions.backends.db"
        key = live_session()
        import_module(settings.SESSION_ENGINE).SessionStore(session_key=key).delete()

        assert self.respond(403, key=key)["Session-Expired"] == "true"


@pytest.mark.django_db
class TestSessionExists:
    def test_a_saved_one(self):
        from isik.django.apps.common.middleware.session import session_exists

        assert session_exists(live_session()) is True
        assert session_exists("no-such-session") is False

    def test_a_signed_cookie_while_it_loads(self, settings):
        from isik.django.apps.common.middleware.session import session_exists

        settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
        key = live_session()

        assert session_exists(key) is True
        assert session_exists(key[:-2] + "xx") is False
