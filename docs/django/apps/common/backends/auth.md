# auth

Helpers for logging in through a backend of your own. isik ships no backend itself: logging in by
username or email depends on which of those a project keeps unique, so it belongs to the project, or
to django-allauth's `AuthenticationBackend` (`ACCOUNT_LOGIN_METHODS`).

## login_through

`login_through(request, user, backend)` is `django.contrib.auth.login()` through a backend *class*
rather than a path string. Django keeps the path a login was handed on the session, and on every
later request treats a session whose path isn't in `AUTHENTICATION_BACKENDS` as anonymous - so a
hard-coded `login(..., backend="...")` whose path isn't listed logs the person in for one response,
silently. `login_through` resolves the class to the path it's listed under, and raises
`ImproperlyConfigured` where the login is written when it isn't listed.

```python
from isik.django.apps.common.backends import login_through

login_through(request, user, EmailModelBackend)
```

- `listed_backend_path(backend)` is the lookup on its own. The class is matched by what each listed
  path imports, so a path through a package that re-exports it counts. A subclass is not its parent.
- **Renaming a listed path logs out every session holding the old one** - the session stores the
  string, not the class. That's a deployment consequence rather than a bug: rename between
  deployments only when ending every session is acceptable, or list both paths for one deployment.
