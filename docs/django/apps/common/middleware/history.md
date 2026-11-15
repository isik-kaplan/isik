# history

`HistoryContextMiddleware` is pghistory's `HistoryMiddleware` plus a way to read the context it
opens: [`open_history_context()`](../db/history.md) returns it while the request is being served.
Use it in place of `HistoryMiddleware`, and subclass it the same way.

```python
# settings.py
MIDDLEWARE = [..., "isik.django.apps.common.middleware.HistoryContextMiddleware"]

# or, adding keys
class TenantHistoryContextMiddleware(HistoryContextMiddleware):
    def get_context(self, request):
        return {**super().get_context(request), "schema": connection.schema_name}
```

- What the reader returns is pghistory's own open context, not a copy taken when the request
  started. A user DRF authenticates in the view is in it, which a copy taken in `get_context()`
  would miss.
- Rows written while serving are annotated exactly as under `HistoryMiddleware`, and
  `history_middleware_installed()` counts it, since it's a subclass.
- A method pghistory doesn't track (`PGHISTORY_MIDDLEWARE_METHODS`) opens no context, so the reader
  returns `None` there.
- Nothing is left behind: once the response is returned or the view raises, the reader returns
  `None`.

## Optional keys - `ISIK["DJANGO"]["HISTORY"]["CONTEXT_KEYS"]`

It also offers keys a project names in `ISIK["DJANGO"]["HISTORY"]["CONTEXT_KEYS"]` - none by default, so the context is
pghistory's own:

```python
ISIK = {
    "DJANGO": {
        "HISTORY": {
            "CONTEXT_KEYS": ["version", "build", "request_id", "correlation_id"],
            "CONTEXT_VERSION": "2.4.0",
            "CONTEXT_BUILD": os.environ["GIT_SHA"],
        },
    },
}
```

- `"version"` - `ISIK["DJANGO"]["HISTORY"]["CONTEXT_VERSION"]`, the version serving the request, so a row says which release
  wrote it.
- `"build"` - `ISIK["DJANGO"]["HISTORY"]["CONTEXT_BUILD"]`, the build itself - its commit, say. Take it with `version`: a
  project that doesn't bump its version on every deploy has many builds at one version, and the version
  alone then tells them apart from nothing.
- Naming `version` or `build` without its setting raises `ImproperlyConfigured` when the middleware loads.
- `"request_id"`, `"correlation_id"`, `"trace_id"` - the request's ids, below.
- Any other name raises `ImproperlyConfigured`, naming it.

## A request's ids

Every request the middleware serves carries two ids, whatever its method:

- `request.request_id` - its own, a uuid7 made here. It's never read from the request, so each request's rows
  stay apart from every other's: a Next.js render calling Django four times leaves four request ids.
- `request.correlation_id` - the id of what the request is part of: the `REQUEST_ID_HEADER` (`X-Request-ID`
  unless set) an edge proxy or a calling server sent, else the request's own id. It groups the whole chain -
  the edge, the render, the Django requests under it, and the tasks they start, since
  [`HistoryContextTask`](../../../celery/README.md) carries the context to a worker.

Named in `CONTEXT_KEYS`, each is kept in the context's metadata beside the rows the request writes, and so is
`"trace_id"`: the Sentry trace the request belongs to, read off its `sentry-trace` header. A trace is
sampled and kept by Sentry for as long as Sentry keeps it; the ids in the context are kept with every row,
unsampled, for as long as the history is. A row whose trace was sampled leads to it, and every row leads to
its request and its chain.

`ISIK["DJANGO"]["HISTORY"]["REQUEST_ID_VALID"]` decides what a sent id may be - a callable taking the value
(or a dotted path to one), `is_uuid` unless set: a UUID, hyphenated or as 32 hex digits, with nothing around
it. nginx's `$request_id` is one. A sent id it doesn't take is replaced by the request's own, never kept or
answered with - so no newline or oversized value reaches the history or a response. The refusal is logged at
DEBUG on `isik.django.apps.common.middleware.history`, naming the header and the value as its `repr()` - where to
look when a proxy's ids never match the correlation ids. A correlation id is the caller's to choose: it groups
requests, and a request is authorized and deduplicated by something else - its credentials, and its [`Idempotency-Key`](../../idempotency/README.md).

`HistoryContextMiddleware.identify(request)` gives a request both ids unless it has them; `get_context()` calls
it, so a subclass's `get_context()` answers for a request a test builds and hands it directly.

### Answering with the id

The response carries the correlation id under `REQUEST_ID_HEADER`, so a browser can log the same id the
history holds. `ISIK["DJANGO"]["HISTORY"]["RETURN_REQUEST_ID"] = False` turns it off.

One of the edge and Django sets it - never both. nginx's `add_header` appends to the headers the upstream
sent rather than replacing them, so both setting it answers with the header twice. The edge's covers what
Django never serves - static files, a 502, the frontend's own pages - so with an edge adding it, set
`RETURN_REQUEST_ID` to False:

```nginx
proxy_set_header X-Request-ID $request_id;          # reuse a sent id, or make one, upstream
add_header X-Request-ID $request_id always;         # and answer with it - RETURN_REQUEST_ID False
```

A real column for an id, on pghistory's context table, is [`AddContextColumn`](../db/history.md#a-column-on-the-context-table---addcontextcolumn).
