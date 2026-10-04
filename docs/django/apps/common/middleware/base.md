# base

`Middleware` - the constructor every hand-written Django middleware opens with, and a `__call__`
around two hooks, so a middleware is only what it does.

```python
from isik.django.apps.common.middleware import Middleware


class ServedBy(Middleware):
    def after(self, request, response):
        response["X-Served-By"] = settings.HOSTNAME
        return response


class Maintenance(Middleware):
    def before(self, request):
        if settings.MAINTENANCE:
            return HttpResponse(status=503)  # answers here; the view never runs
```

- `before(request)` runs first. A response it returns answers the request - the rest of the chain
  and the view never run, and `after()` still sees it. `None` carries on.
- `after(request, response)` runs on the way out and returns the response to send.
- Django's other hooks - `process_view`, `process_exception`, `process_template_response` - stay
  Django's: define them on a subclass as on any middleware. Override `__call__` when the work has to
  wrap the view, as a context manager or a `try`/`finally` does.
