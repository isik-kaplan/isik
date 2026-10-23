from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation

from isik._internal.translation import gettext as _


class CookieORHeaderSessionMiddleware(SessionMiddleware):
    """
    SessionMiddleware variant that accepts the session key from either the session
    cookie or a header, useful when the same API is consumed by both browsers and
    non-browser clients. Raises if a request supplies both and they disagree.

    Requires settings.SESSION_HEADER_NAME to be set to the header name to read - raises
    ImproperlyConfigured at middleware init time (not on the first request) if it's missing.
    """

    def __init__(self, get_response):
        if not hasattr(settings, "SESSION_HEADER_NAME"):
            raise ImproperlyConfigured(
                _("%(middleware)s requires settings.SESSION_HEADER_NAME to be set.")
                % {"middleware": self.__class__.__name__}
            )
        super().__init__(get_response)

    def process_request(self, request):
        session_key = self.get_session_key(request)
        request.session = self.SessionStore(session_key)

    @staticmethod
    def get_session_key(request):
        cookie_session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        header_session_key = request.headers.get(settings.SESSION_HEADER_NAME)
        if cookie_session_key and header_session_key:
            if cookie_session_key != header_session_key:
                raise SuspiciousOperation(_("Session key mismatch"))
        return cookie_session_key or header_session_key


# Where DeadSessionCookieMiddleware says the cookie was dead, unless DEAD_SESSION_HEADER names another.
DEAD_SESSION_HEADER = "Session-Expired"


class DeadSessionCookieMiddleware:
    """
    A 401 or 403 to a request that sent a session cookie and still isn't signed in means the cookie is
    dead - expired, signed out elsewhere, its session rotated away. The response deletes it and says so
    in a header (`DEAD_SESSION_HEADER`, `Session-Expired` unless set), so a browser stops sending it and
    a client can tell "sign in again" from "you may not".

    Place it after `AuthenticationMiddleware`, which decides whether the request is signed in. It
    covers every view, DRF's or not - an admin page or an allauth one as well as an API. Whether to
    install it is the project's.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        signed_in = user is not None and user.is_authenticated
        if response.status_code in (401, 403) and settings.SESSION_COOKIE_NAME in request.COOKIES and not signed_in:
            # Deleted with the attributes it was set with, or a browser keeps it.
            response.delete_cookie(
                settings.SESSION_COOKIE_NAME,
                path=settings.SESSION_COOKIE_PATH,
                domain=settings.SESSION_COOKIE_DOMAIN,
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
            response[getattr(settings, "DEAD_SESSION_HEADER", DEAD_SESSION_HEADER)] = "true"
        return response
