from django.conf import settings
from django.contrib.auth import login
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _


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
