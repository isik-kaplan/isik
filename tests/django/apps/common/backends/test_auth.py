import pytest
from django.contrib.auth import get_user_model

from isik.django.apps.common.backends.auth import UsernameOREmailModelBackend


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


def test_accepts_the_username_field_name_via_kwargs(user):
    # No username= kwarg here - it always binds to the explicit parameter, never **kwargs.
    user_model = get_user_model()
    backend = UsernameOREmailModelBackend()
    kwargs = {user_model.USERNAME_FIELD: getattr(user, user_model.USERNAME_FIELD)}
    assert backend.authenticate(None, password="password", **kwargs) == user
