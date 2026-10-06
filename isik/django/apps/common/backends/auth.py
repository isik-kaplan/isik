from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _


class UsernameOREmailModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        if username is None:
            username = kwargs.get(user_model.USERNAME_FIELD)
        if not username:
            return None
        try:
            lookup = Q(**{user_model.EMAIL_FIELD: username}) | Q(**{user_model.USERNAME_FIELD: username})
            user = user_model._default_manager.get(lookup)
        except user_model.DoesNotExist:
            # Run the default password hasher once to reduce the timing
            # difference between an existing and a non-existing user (#20760).
            user_model().set_password(password)
            # This instance is local and never saved/returned - set_password()'s only real effect
            # is the CPU time it burns, not the resulting hash, so which value it hashes (the
            # caller's password vs. e.g. None) is unobservable through any assertion.
        else:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user


def listed_backend_path(backend):
    """
    The path `backend` (a class) is listed under in `AUTHENTICATION_BACKENDS` - matched by the class
    it imports, so a path through a package that re-exports it counts. `ImproperlyConfigured` when
    none lists it.
    """
    for path in settings.AUTHENTICATION_BACKENDS:
        if import_string(path) is backend:
            return path
    dotted = f"{backend.__module__}.{backend.__qualname__}"
    raise ImproperlyConfigured(
        _(
            "%(backend)s isn't in AUTHENTICATION_BACKENDS, so a session logged in through it is anonymous "
            "from its next request."
        )
        % {"backend": dotted}
    )


def login_through(request, user, backend):
    """
    `django.contrib.auth.login()` through `backend`, a class rather than a path string. Django stores
    the path it's handed on the session and, on every later request, treats a session whose path
    isn't listed in `AUTHENTICATION_BACKENDS` as anonymous - so a hard-coded path that isn't listed
    logs the person in for one response, silently. This raises where the login is written instead.
    """
    login(request, user, backend=listed_backend_path(backend))
