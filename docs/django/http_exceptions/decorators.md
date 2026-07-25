# decorators

`errorify(error)` decorates a view — function-based or class-based — so that instead of returning
its response normally, it raises `error` with that response attached (via `with_response`). Useful
for views that should always look like an error to anything downstream (middleware, tests
expecting an exception) while still producing real response content.

```python
from isik.django.http_exceptions.decorators import errorify
from isik.django.http_exceptions.exceptions import HTTPExceptions

@errorify(HTTPExceptions.GONE)
class Retired(View):
    def get(self, request):
        return HttpResponse("this endpoint is retired")
```

- On a class-based view, wraps `dispatch` (via `method_decorator`) rather than each HTTP-method
  handler individually.
