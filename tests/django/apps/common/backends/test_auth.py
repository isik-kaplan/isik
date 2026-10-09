import pytest
from django.contrib.auth import BACKEND_SESSION_KEY, get_user, login
from django.core.exceptions import ImproperlyConfigured

from isik.django.apps.common.backends.auth import listed_backend_path, login_through
from tests.testapp.backends import EmailModelBackend


pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="password")


class TestLoggingInThroughABackend:
    """
    Django keeps the backend path a login was handed on the session, and treats any later request
    whose path isn't in AUTHENTICATION_BACKENDS as anonymous - silently. login_through() takes the
    class and refuses an unlisted one where the login is written.
    """

    MODULE_PATH = "tests.testapp.backends.auth.EmailModelBackend"
    # The same class through the package that re-exports it.
    PACKAGE_PATH = "tests.testapp.backends.EmailModelBackend"

    @staticmethod
    def request_with_a_session(rf):
        from django.contrib.sessions.middleware import SessionMiddleware

        request = rf.get("/")
        SessionMiddleware(lambda request: None).process_request(request)
        return request

    def test_finds_the_path_a_backend_is_listed_under(self, settings):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", self.MODULE_PATH]

        assert listed_backend_path(EmailModelBackend) == self.MODULE_PATH

    def test_a_path_through_a_re_exporting_package_counts(self, settings):
        settings.AUTHENTICATION_BACKENDS = [self.PACKAGE_PATH]

        assert listed_backend_path(EmailModelBackend) == self.PACKAGE_PATH

    def test_a_subclass_is_not_its_parent(self, settings):
        class Narrower(EmailModelBackend):
            pass

        settings.AUTHENTICATION_BACKENDS = [self.MODULE_PATH]

        with pytest.raises(ImproperlyConfigured):
            listed_backend_path(Narrower)

    def test_an_unlisted_backend_is_refused_by_name(self, settings):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

        with pytest.raises(ImproperlyConfigured) as raised:
            listed_backend_path(EmailModelBackend)

        assert str(raised.value) == (
            f"{self.MODULE_PATH} isn't in AUTHENTICATION_BACKENDS, so a session logged in through it is "
            "anonymous from its next request."
        )

    def test_a_login_through_a_listed_backend_lasts(self, settings, rf, user):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", self.PACKAGE_PATH]
        request = self.request_with_a_session(rf)

        login_through(request, user, EmailModelBackend)

        assert request.session[BACKEND_SESSION_KEY] == self.PACKAGE_PATH
        assert get_user(request) == user

    def test_a_login_through_an_unlisted_backend_never_happens(self, settings, rf, user):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
        request = self.request_with_a_session(rf)

        with pytest.raises(ImproperlyConfigured):
            login_through(request, user, EmailModelBackend)

        assert BACKEND_SESSION_KEY not in request.session

    def test_what_it_prevents(self, settings, rf, user):
        # Plain login() with an unlisted path: logged in for this response, anonymous on the next.
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
        request = self.request_with_a_session(rf)

        login(request, user, backend=self.MODULE_PATH)

        assert get_user(request).is_anonymous
