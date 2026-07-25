# middleware

Two independent middlewares: `RequestContextMiddleware` stashes the current request in a
`ContextLocal` so code without direct request access (serializers, signal handlers) can retrieve
it via `get_current_request()`; `ExceptionHandlerMiddleware` turns any raised
`HTTPExceptions.BASE_EXCEPTION` into a response.

```python
MIDDLEWARE = [
    "isik.django.http_exceptions.middleware.RequestContextMiddleware",
    "isik.django.http_exceptions.middleware.ExceptionHandlerMiddleware",
    ...,
]
```

```python
from isik.django.http_exceptions.middleware import get_current_request

request = get_current_request()  # None outside of a request
```

- `ExceptionHandlerMiddleware.process_exception` picks a response in order: the exception's own
  attached `.response` (from `with_response`/`with_content`/`with_json`), else its registered
  default view, else a plain response built from `.description`/`.status` — and always runs
  registered error handlers first, regardless of which branch is used.
- `get_current_request()` returns `None` outside a request handled by `RequestContextMiddleware`,
  and again once that request finishes — it resets on exit even if `get_response` raised.
