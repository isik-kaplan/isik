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
