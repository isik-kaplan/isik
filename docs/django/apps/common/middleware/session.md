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
