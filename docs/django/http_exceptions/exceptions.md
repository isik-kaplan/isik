# exceptions

`HTTPExceptions` builds one raisable `HTTPException` subclass per `http.HTTPStatus` member
(`HTTPExceptions.NOT_FOUND`, `HTTPExceptions.FORBIDDEN`, ...) via the `transform` metaclass, each
carrying `status`/`description` from the matching `HTTPStatus` value. An `HTTPException` instance
can carry a response (`with_response`/`with_content`/`with_json`), and a class can register a
default view or error handlers.

```python
from isik.django.http_exceptions import HTTPExceptions

raise HTTPExceptions.NOT_FOUND.with_json({"error": "no such widget"})
```

## HTTPExceptions.from_status

Looks up the exception class for a numeric HTTP status code.

```python
HTTPExceptions.from_status(404) is HTTPExceptions.NOT_FOUND
```

## HTTPExceptions.register_base_exception

Reassigns the `__bases__` of every generated exception class to `new_exception`, to globally swap
in a custom base after the fact instead of subclassing each status individually.

```python
HTTPExceptions.register_base_exception(MyCustomHTTPException)
```

- `HTTPExceptions` itself can't be instantiated (`__abstract__ = True`) — always raise/reference
  one of its generated members instead.
- `with_response(response)`/`with_content(content)`/`with_json(json_data)` are classmethods that
  build and return an *instance* to raise, forcing `response.status_code` to the exception's own
  `status` in the process.
- `register_error_handler(handler)`/`remove_error_handler(handler)` register per-class (not
  global) callables, invoked by `ExceptionHandlerMiddleware.process_exception` before it builds a
  response.
