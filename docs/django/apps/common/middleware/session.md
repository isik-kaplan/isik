# session

`CookieORHeaderSessionMiddleware` is a `SessionMiddleware` variant that reads the session key from
either the session cookie or a header (`settings.SESSION_HEADER_NAME`), for APIs consumed by both
browsers and non-browser clients that can't send cookies.

```python
# settings.py
SESSION_HEADER_NAME = "X-Session-Key"
MIDDLEWARE = ["isik.django.apps.common.middleware.session.CookieORHeaderSessionMiddleware", ...]
```

- Raises `SuspiciousOperation` if a request supplies both cookie and header and they disagree —
  silently preferring one could mask a real client bug.
- Requires `settings.SESSION_HEADER_NAME` to be set; there's no default.

## DeadSessionCookieMiddleware

A 401 or 403 to a request that sent a session cookie and still isn't signed in means the cookie is
dead - expired, signed out elsewhere, its session rotated away. `DeadSessionCookieMiddleware` deletes
it on that response (with the path, domain and `SameSite` it was set with, or a browser keeps it) and
says so in a header, so a browser stops sending it and a client can tell "sign in again" from "you
may not":

```python
MIDDLEWARE = [
    ...,
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "isik.django.apps.common.middleware.DeadSessionCookieMiddleware",
]
DEAD_SESSION_HEADER = "Session-Expired"   # the default
```

- Place it after `AuthenticationMiddleware`, which decides whether the request is signed in.
- It covers every view, DRF's or not - an admin page or an allauth one as well as an API.
- A 401/403 to a signed-in request, or one without the cookie, is left as it is.
