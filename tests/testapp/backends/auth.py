from django.contrib.auth.backends import ModelBackend


class EmailModelBackend(ModelBackend):
    """A project's own backend, for login_through() tests."""
