# preconditions

`PreconditionMixin` - optimistic concurrency on a DRF view over one model, with RFC 9110's conditional
requests. A write names the version of the row it was made against, and is refused if the row has
moved on since. A page loaded before someone else saved can't silently overwrite what they saved (a
lost update).

```python
from isik.django.drf.viewsets import BaseModelViewSet, PreconditionMixin


class WidgetViewSet(PreconditionMixin, BaseModelViewSet):
    model = Widget
    endpoint = "widgets"
    serializer_class = WidgetSerializer
```

```
GET    /widgets/7/                                -> 200  ETag: "1792585815123456"
GET    /widgets/7/  If-None-Match: "1792...456"   -> 304
PATCH  /widgets/7/  If-Match: "1792...456"        -> 200  ETag: "1792585815654321"
PATCH  /widgets/7/  If-Match: "1792...456"        -> 412  {"detail", "code", "current"}
PATCH  /widgets/7/                                -> 428  {"detail", "code"}
```

The tag is the model's [concurrency token](../../apps/common/db/concurrency.md) - `updated_at` on a
`BaseModel`, `version` on a `VersionedModel` - as an opaque, strong `ETag`. A client sends back what
it got, and never builds one from `updated_at` (a JavaScript `Date` drops the microseconds). Add a
[`ConcurrencyTokenField`](../serializers/concurrency_token.md) to the serializer for the same string in
the body, so a list's items carry theirs.

Needs the request served in a transaction - turn on `ATOMIC_REQUESTS`. A write that has to lock the row
raises `ImproperlyConfigured` without one.

## Which requests

- **PUT, PATCH, DELETE, and POST to one object** (a `detail=True` action) need `If-Match`. Without it:
  `428 {"detail": "...", "code": "precondition_required"}` (see `required` under Options).
- **`retrieve`** (`precondition_read_actions`, which also holds `None`, the GET of a view that isn't a
  viewset) sends `ETag`, and answers `If-None-Match` naming the current version with a 304 and no body
  (see `not_modified`).
  Another detail GET (an action) shows something other than the row, so it sends none.
- **A create** needs nothing, and sends its new row's `ETag` with the 201.
- **A list, and a POST to no one object**, have no version to name or send.

`precondition_exempt_actions = {action: reason}` leaves an action out. Each reason is a `NoPrecondition`
exemption (rule `isik.precondition`), held to its 40-character floor and listed by `manage.py
exemptions`. An exempt write still answers with the version it left. `precondition_methods` (PUT,
PATCH, DELETE, POST) narrows which methods ask.

## The comparison

It runs in `get_object()`, after authentication, permissions and the 404, so those answer first. There
the row is locked (`SELECT ... FOR UPDATE`) and read again, so the version compared is the one the write
lands on. A write that landed between `get_object()`'s read and the lock is caught, and a second write
made against the same version waits for the first and then gets a 412. The lock goes through the
model's base manager, which joins nothing: Postgres refuses `FOR UPDATE` on the nullable side of an
outer join, which a `select_related()` in the view's queryset makes.

- **A list of tags** (`If-Match: "a", "b"`) matches if any one does.
- **`If-Match: *`** matches any current version, unless `allows_any` is off (below).
- **A `W/` prefix is ignored**, unless `ignores_weak_tags` is off (below).

A stale tag is refused with the row as it is now, as `retrieve` shows it, and its current `ETag`:

```json
412  ETag: "1792585815654321"
{
  "detail": "This changed since the version the request was made against.",
  "code": "precondition_failed",
  "current": {"id": "…", "name": "nut", "etag": "\"1792585815654321\""}
}
```

Both refusals are rendered by the mixin rather than by DRF's exception handler, which would leave out
`code` and render `current`'s values as error strings. They roll the request's transaction back as that
handler would.

## Options

Each option is a setting, `PRECONDITION_<OPTION>`, and a view's own `precondition_<option>` overrides
it. The attribute defaults to None, which defers to the setting, and a missing setting is the default
below. `PreconditionMixin.precondition_option(name)` reads one as a view sees it.

```python
# settings.py - every view
PRECONDITION_MISSING_ROW_STATUS = 412


class LegacyImportViewSet(PreconditionMixin, BaseModelViewSet):
    precondition_required = False  # this view only
```

| Option | Default | What it does |
|---|---|---|
| `ignores_weak_tags` | `True` | A `W/` prefix is ignored when comparing `If-Match`. Off: RFC 9110's strong comparison, where a weak tag never matches. |
| `required` | `True` | A write without `If-Match` is a 428. Off: it goes through unchecked. A write that sends `If-Match` is still checked. |
| `allows_any` | `True` | `If-Match: *` matches any current version. Off: it's refused with a 428 - the write must name the version it was made against. |
| `missing_row_status` | `404` | What a write with `If-Match` gets when the row isn't there. `412` is RFC 9110's answer: the condition failed, with `current` null and no `ETag`. |
| `not_modified` | `True` | `If-None-Match` naming the current version answers `retrieve` with a 304. Off: the full response, still with its `ETag`. |

Why the defaults:

- **`ignores_weak_tags`**: the tag names the row's version, not the bytes of one response, so encoding
  can't make it stale. Compression weakens it on the way out - Django's `GZipMiddleware`, nginx's gzip -
  and a client sending back what it got would then be refused on every write. Turn it off only when
  nothing between the server and the client rewrites `ETag`.
- **`required`**: an optional `If-Match` protects only the clients that remember to send it. The lost
  update the mixin exists for comes from the ones that don't. Turn it off while clients are adopting it.
- **`allows_any`**: `*` is RFC 9110's "whatever is current". It's how a client that means to overwrite
  says so, which is different from a client that forgot to say anything. Turn it off where every write
  must have been made against a version actually seen.
- **`missing_row_status`**: 404 is what the same request says without `If-Match`, and what DRF says
  everywhere else. 412 is the RFC's answer, for a client that handles "the row changed" and "the row is
  gone" the same way.
- **`not_modified`**: a 304 spares sending a row the client already holds. Turn it off when a cache in
  front mishandles 304s.

The system check **`isik.E003`** refuses a value an option can't take: a switch that isn't `True` or
`False`, or a `missing_row_status` other than 404 or 412. **`isik.W001`** warns about a view that refuses
`If-Match: *` but doesn't require `If-Match`: a client that would send `*` leaves the header out
instead, so refusing `*` protects nothing. A view with `required` off is listed as exempt by
`precondition_coverage()`, its reason saying so.

## After the write

A successful write sends the token the database left as the new `ETag`. It's read back, since Django
doesn't read what a trigger set - from the database writes are routed to, where the write is.
`perform_update()` also reads the row back before the body is rendered, so the body agrees. A delete
sends none.

## With `IdempotencyMixin`

A retry with the same `Idempotency-Key` is replayed in `initial()`, before `get_object()` runs, so it
gets its first success back instead of a 412 for the version its own first attempt moved on - in either
order of the two mixins. A 412 or 428 doesn't spend the key. A replay carries no `ETag` header: the
row's current one is in a by-reference replay's body.

## Caches

The tag is the row's version, so two users shown different fields of one row get the same tag. A
shared cache must not answer one user's `If-None-Match` with another's page. That's the response's
`Vary` (`Cookie`, `Authorization`) to say, as for any per-user response.

## Checking a project

- The system check **`isik.E002`** holds every routed `PreconditionMixin` view to a token it can trust:
  its model is found (`precondition_model`, else `model`, else the queryset's), the token passes
  [`concurrency_token_problems()`](../../apps/common/db/concurrency.md#concurrency_token_problemsmodel),
  and no serializer the view declares (`serializer_class`, `serializer_class_action_map`) lets a request
  write it. `precondition_problems(urlconf=None)` gives the same list.
- [`precondition_coverage()`](../coverage.md#precondition_coverageurlconfnone) lists which routed writes
  ask for `If-Match`, which are exempt and why, and which don't - for a project's own test or check.
- [`AutoSchema`](../spectacular.md#preconditionmixin) documents the headers and the 304/412/428
  responses.

## For a client

- Read the token from the `ETag` header, or from the body's `etag` field (a `ConcurrencyTokenField`),
  which compression never touches. A list's items carry it only in the body.
- Send it back exactly as received, in `If-Match`, on every write to one object.
- On a 412, show `current` - or merge into it - and retry with its `ETag`.
- Behind CORS, the browser needs `ETag` in `Access-Control-Expose-Headers` and `If-Match` /
  `If-None-Match` in `Access-Control-Allow-Headers`. That's the project's CORS configuration.
