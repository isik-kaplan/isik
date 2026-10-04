import pytest
from django.contrib.auth import BACKEND_SESSION_KEY, get_user, get_user_model, login
from django.core.exceptions import ImproperlyConfigured

from isik.django.apps.common.backends.auth import UsernameOREmailModelBackend, listed_backend_path, login_through


pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="alice", email="alice@example.com", password="password")


def test_authenticates_by_the_username_field_value(user):
    # tests.testapp.EmailUser sets USERNAME_FIELD = "email", so this checks the email, not "username".
    user_model = get_user_model()
    backend = UsernameOREmailModelBackend()
    value = getattr(user, user_model.USERNAME_FIELD)
    assert backend.authenticate(None, username=value, password="password") == user


def test_authenticates_by_email(user):
    backend = UsernameOREmailModelBackend()
    assert backend.authenticate(None, username="alice@example.com", password="password") == user


def test_rejects_wrong_password(user):
    backend = UsernameOREmailModelBackend()
    assert backend.authenticate(None, username="alice@example.com", password="wrong") is None


def test_rejects_unknown_username(db):
    backend = UsernameOREmailModelBackend()
    assert backend.authenticate(None, username="unknown", password="password") is None


def test_rejects_inactive_user(user):
    user.is_active = False
    user.save()
    backend = UsernameOREmailModelBackend()
    assert backend.authenticate(None, username="alice@example.com", password="password") is None


def test_neither_username_nor_a_matching_kwarg_fails_to_match_rather_than_raising(db):
    backend = UsernameOREmailModelBackend()
    assert backend.authenticate(None) is None


def test_accepts_the_username_field_name_via_kwargs(user):
    # No username= kwarg here - it always binds to the explicit parameter, never **kwargs.
    user_model = get_user_model()
    backend = UsernameOREmailModelBackend()
    kwargs = {user_model.USERNAME_FIELD: getattr(user, user_model.USERNAME_FIELD)}
    assert backend.authenticate(None, password="password", **kwargs) == user


def test_matches_by_either_the_username_field_or_the_email_field_not_both(monkeypatch, user):
    # EmailUser's EMAIL_FIELD ("email", inherited from AbstractUser) happens to equal its own
    # USERNAME_FIELD override ("email" too) - every other test here authenticates by a value
    # that satisfies both sides of the lookup at once, so an OR-vs-AND regression wouldn't show up
    # through them. Point EMAIL_FIELD at the model's separate "username" field instead, so a value
    # that matches only that side (not USERNAME_FIELD/"email") proves the lookup is really OR.
    user_model = get_user_model()
    monkeypatch.setattr(user_model, "EMAIL_FIELD", "username")
    backend = UsernameOREmailModelBackend()
    assert backend.authenticate(None, username=user.username, password="password") == user


class TestLoggingInThroughABackend:
    """
    Django keeps the backend path a login was handed on the session, and treats any later request
    whose path isn't in AUTHENTICATION_BACKENDS as anonymous - silently. login_through() takes the
    class and refuses an unlisted one where the login is written.
    """

    MODULE_PATH = "isik.django.apps.common.backends.auth.UsernameOREmailModelBackend"
    # The same class through the package that re-exports it.
    PACKAGE_PATH = "isik.django.apps.common.backends.UsernameOREmailModelBackend"

    @staticmethod
    def request_with_a_session(rf):
        from django.contrib.sessions.middleware import SessionMiddleware

        request = rf.get("/")
        SessionMiddleware(lambda request: None).process_request(request)
        return request

    def test_finds_the_path_a_backend_is_listed_under(self, settings):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", self.MODULE_PATH]

        assert listed_backend_path(UsernameOREmailModelBackend) == self.MODULE_PATH

    def test_a_path_through_a_re_exporting_package_counts(self, settings):
        settings.AUTHENTICATION_BACKENDS = [self.PACKAGE_PATH]

        assert listed_backend_path(UsernameOREmailModelBackend) == self.PACKAGE_PATH

    def test_a_subclass_is_not_its_parent(self, settings):
        class Narrower(UsernameOREmailModelBackend):
            pass

        settings.AUTHENTICATION_BACKENDS = [self.MODULE_PATH]

        with pytest.raises(ImproperlyConfigured):
            listed_backend_path(Narrower)

    def test_an_unlisted_backend_is_refused_by_name(self, settings):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

        with pytest.raises(ImproperlyConfigured) as raised:
            listed_backend_path(UsernameOREmailModelBackend)

        assert str(raised.value) == (
            f"{self.MODULE_PATH} isn't in AUTHENTICATION_BACKENDS, so a session logged in through it is "
            "anonymous from its next request."
        )

    def test_a_login_through_a_listed_backend_lasts(self, settings, rf, user):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", self.PACKAGE_PATH]
        request = self.request_with_a_session(rf)

        login_through(request, user, UsernameOREmailModelBackend)

        assert request.session[BACKEND_SESSION_KEY] == self.PACKAGE_PATH
        assert get_user(request) == user

    def test_a_login_through_an_unlisted_backend_never_happens(self, settings, rf, user):
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
        request = self.request_with_a_session(rf)

        with pytest.raises(ImproperlyConfigured):
            login_through(request, user, UsernameOREmailModelBackend)

        assert BACKEND_SESSION_KEY not in request.session

    def test_what_it_prevents(self, settings, rf, user):
        # Plain login() with an unlisted path: logged in for this response, anonymous on the next.
        settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
        request = self.request_with_a_session(rf)

        login(request, user, backend=self.MODULE_PATH)

        assert get_user(request).is_anonymous
