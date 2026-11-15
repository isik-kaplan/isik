# Sync - `isik.django.apps.sync`

Offline sync's server. A client keeps a local copy of the rows a person sees, pulls what changed since its
cursor, and pushes the writes it queued while offline. The wire contract is isik's sync protocol, version 1 -
`protocol/sync/v1` in isik-ts, whose client speaks it - and isik's server passes every one of its fixtures
(pinned in `tests/protocol/sync/v1`).

```
pip install isik[sync]
```

- [Setup](#setup)
- [Declaring what syncs](#declaring-what-syncs)
- [Scopes and memberships](#scopes-and-memberships)
- [The log](#the-log)
- [The feed](#the-feed)
- [Push](#push)
- [x-sync in the API schema](#x-sync-in-the-api-schema)
- [Settings](#settings)
- [Operations](#operations)
- [Owning the log](#owning-the-log)

## Setup

```python
INSTALLED_APPS = [..., "rest_framework", "isik.django.apps.sync"]

# urls.py
from rest_framework.routers import SimpleRouter
from isik.django.apps.sync.drf import SyncViewSet

sync = SimpleRouter(trailing_slash=False)
sync.register("sync", SyncViewSet, basename="sync")      # /sync/capabilities, /sync/changes, /sync/push
urlpatterns = [..., *sync.urls]

SPECTACULAR_SETTINGS = {
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "isik.django.apps.sync.spectacular.add_sync_extensions",
    ],
}
```

`SyncViewSet` takes the project's authentication, and needs an authenticated person: a stranger is answered
401, which pauses a client. It's served outside `ATOMIC_REQUESTS` - push commits each entry on its own - with
the reason `isik.E011` asks for.

## Declaring what syncs

```python
from isik.django.apps.sync.declarations import Memberships, syncs

@syncs("tasks", scope="owner")
class Task(BaseModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, ...)
    title = models.CharField(...)

@syncs("notes", scope="team", memberships=Memberships("teams.Membership", user="user", scope="team"))
class Note(BaseModel):
    team = models.ForeignKey("teams.Team", ...)
```

`syncs(name=None, *, scope, kind=None, memberships=None, viewset=None, indexed=None)`:

- `name` - what the feed and push call the model; its model name unless given.
- `scope` - the field holding the id of the scope a row is in.
- `kind` - the scope's kind: `"user"` for a key to the user model, else the name of the model the key points
  at, unless given. A scope field that isn't a key needs one.
- `memberships` - how people join a scope of the kind - see below.
- `viewset` - the viewset, or its dotted path, serving the model's REST API. Unset, the one
  `ViewSetRegistryMixin` registered for the model.
- `indexed` - the column lists a client indexes its copy by. Unset, each key and each index of the model's own
  fields.

A synced model's REST API is what the feed reads rows with and push writes them through, so it needs:

- **a flat representation**: keys as ids, nothing nested, its key and its scope field among its fields;
- **a concurrency token** (`updated_at` on a `BaseModel`) - the feed sends each row's `ETag`, and push's writes
  name it in `If-Match`. A version covering relations (`concurrency_relations`) works: isik gives each covered
  relation's table a trigger logging the synced row whenever one of its rows changes. A version read from
  `concurrency_parts()` doesn't - what they read changes with nothing the log hears of;
- **`ClientPrimaryKeyMixin`** on its create serializer: the client makes a row's id offline and refers to the
  row by it in the writes it queues after. An id a row has already is a 409, `primary_key_taken`; an id not sent
  is made as ever.

`isik.E027` refuses a declaration missing any of it.

## Scopes and memberships

A scope is a string, `"<kind>:<id>"`. A person is in their own, `user:<their pk>`, always, and in each scope a
membership row puts them in:

```python
Memberships("teams.Membership", user="user", scope="team", condition=Q(active=True))
```

One row of the membership model per person (`user`, a key to the user model) in a scope (`scope`). With a
`condition` - a `Q` over the membership model's own fields - only a row meeting it counts. A row coming to put
its person in its scope - written, or updated to meet the condition - grants the scope; a row ceasing to -
deleted, or updated not to meet it - revokes it; a row moved to another person or scope does both. Every kind
but `"user"` needs its memberships, declared on one of the models scoped by it.

## The log

Each change to a synced row writes one entry of the sync log, by a trigger, in the transaction making the
change - raw SQL, `QuerySet.update()` and the admin's writes too:

| field | holds |
|---|---|
| `xid`, `seq` | the entry's position: the writing transaction's id, and a number the database gives each entry |
| `op` | `upsert` or `delete` of a row; `scope_granted` or `scope_revoked` for a person; `pruned`, where pruning cut the log |
| `model`, `object_id` | the row's model, as `syncs()` names it, and its primary key |
| `scope` | the row's scope after the change, or the scope granted or revoked |
| `old_scope` | the scope a row left, when an update moved it |
| `member` | the person granted or revoked the scope |
| `created_at` | when the change was made - what retention prunes by |

It holds no row data: the feed reads each row as its REST API shows it when it serves the change. An update
changing nothing writes no entry. The log is append-only, and refuses a `TRUNCATE`.

Transactions commit out of order, so an entry of one still open may yet appear below an entry already served.
The feed serves only entries below the **watermark**: the lowest id among the transactions of this database
still open, which only rises. Every transaction below it has ended, so no entry can be added there - a cursor
never passes a change that commits late. A transaction open in another database of the cluster holds nothing
back.

## The feed

`GET /sync/changes?cursor=&limit=&snapshot=` answers `{changes, cursor, has_more, remaining}` with an `ETag`;
asked again with `If-None-Match` naming it, a page that hasn't changed is a 304.

- A missing cursor starts a **full snapshot** of every row the person sees, read at the watermark, framed by
  `snapshot_begin` and `snapshot_end` - the feed goes on from there. A scope granted starts a snapshot of the
  scope; `snapshot=<model>` one of the model.
- A snapshot is served whole, across as many pages as it takes, before the log goes on - no live change
  interleaves with one. A scope revoked while its snapshot is served ends it, with `scope_revoked`.
- Within a page, several changes to one row collapse to the latest. Each row is read through the model's
  viewset - its `get_queryset()`, its permissions, its `retrieve` serializer - so the feed and the API agree:
  a row the person can't see now goes out as a `delete`.
- `limit` counts row changes; scope and snapshot events ride along. `PAGE_LIMIT` caps what a client asks.
- For a progress bar: `remaining` is at most how many row changes are still to come after the page, outside any
  snapshot - the log's entries of the person's scopes past the cursor, below the watermark, those an open
  snapshot holds back among them. It's one count over the log, so it's never below what comes: a page collapses
  a row's entries, and a row the person can't see is served as one `delete` or none. It's `0` when `has_more` is
  false. Each `snapshot_begin` carries `total`, the rows the snapshot serves across every model it covers -
  counted once, when it opens, through each model's `get_queryset()`; a scope revoked while it's served ends it
  with fewer.
- The cursor is signed, and opaque to the client. One before what pruning removed - or not signed here - is a
  410, `sync_cursor_expired`, and the client pulls a snapshot again. A model `snapshot=` names that isn't synced
  is passed over.

## Push

`POST /sync/push` with `{entries: [{key, method, model, id, body, if_match}]}` answers `{results: [...]}`, one
result per entry, in entry order: `{key, status, etag?, data? | current? | errors?}`. `GET /sync/capabilities`
advertises the backend running it and its limits, `null` for none.

What runs the entries is a backend - `ISIK['DJANGO']['SYNC']['PUSH']`'s `BACKEND`, a `PushBackend` subclass,
built with its `OPTIONS`. isik's own, `ViewPushBackend`, runs each entry through its model's REST view - its
serializer, permissions, idempotency and preconditions - in order, each in a transaction of its own:

- An entry refused, or raising, is that entry's result alone (a raise is a 500, `{"code": "error"}`, logged);
  it commits nothing - not its write, not its idempotency key - and the next entry runs.
- An entry's request is the push's own - its user, session and whatever middleware set on it - with the
  entry's method, path, body, `Idempotency-Key` and `If-Match`. An entry's `key` that isn't a UUID is made one
  (a UUID5 of it), so `"k1"` names the same claim every time.
- Each entry gets a history context of its own: the request's, with `sync_entry`, its place in the push.
- A view's throttles don't run for an entry - they'd count each one as a request - but those carrying
  `PerEntryThrottle`, which guard a cost each write runs up, do, against the person's real rate: an entry over
  one is answered 429 with its wait, and the push goes on.

```python
class TextsThrottle(PerEntryThrottle, UserRateThrottle):
    scope = "texts"
```

The options every backend takes, each None - no limit - unless set:

- `MAX_ENTRIES`, `MAX_BYTES` - the most entries, and body bytes, one push holds. Over either is a 413,
  `sync_push_too_large`, with both limits. `MAX_BYTES` must fit `DATA_UPLOAD_MAX_MEMORY_SIZE`, which refuses a
  bigger body before a push sees it.
- `THROTTLE_SCOPE` - a DRF throttle scope (a rate in `DEFAULT_THROTTLE_RATES`) counting each entry a person
  pushes as a request.

## x-sync in the API schema

`add_sync_extensions` adds `x-sync` to each routed synced model's component schema - everything a client needs
to keep a local table of it:

```json
{"protocol": 1, "model": "tasks", "path": "/api/tasks/", "primary_key": "id",
 "scope": {"kind": "user", "field": "owner"},
 "columns": {"id": "uuid", "owner": "uuid", "title": "string", "done": "boolean", "project": "uuid"},
 "indexed": [["owner"], ["project"], ["done", "title"]], "hash": "9f2c..."}
```

`hash` covers the rest; a client re-snapshots a model whose hash moved.

## Settings

`ISIK['DJANGO']['SYNC']`:

| key | holds | default |
|---|---|---|
| `LOG_MODEL` | the sync log model | isik's |
| `RETENTION` | how long a change stays in the log, a timedelta | None - kept |
| `OFFLINE_PERIOD` | the longest a client is meant to come back after, a timedelta | None |
| `PAGE_LIMIT` | the most row changes one page carries | None - what a client asks |
| `LAG_WARNING` | how long an open transaction may hold the feed back before each page logs a warning | None - no warning |
| `PUSH` | `{"BACKEND": ..., "OPTIONS": {...}}` | `ViewPushBackend`, no limits |

With `OFFLINE_PERIOD` set, `isik.E027` holds `RETENTION` to at least it - a client offline that long would come
back to a cursor pruning passed - and `ISIK['DJANGO']['IDEMPOTENCY']['RETENTION']` to longer than it - a client
pushing keys whose claims are gone runs a write whose answer it lost twice.

## Operations

- `prune_sync_log(batch=None)` deletes the entries older than `RETENTION`, up to `batch` of them, and marks
  where it cut. Run it from your scheduler. Only entries of transactions below the watermark go.
- `sync_feed_lag()` answers how long the oldest open transaction of the database has held the feed back, and its
  process id - `FeedLag(lag, pid)`, or None - for a health check. One connection idle in a transaction stalls
  every person's feed, and so does any long transaction that has written - a Celery task, a migration.
  `LAG_WARNING` logs a warning naming the process.
- Set Postgres's `idle_in_transaction_session_timeout` - a connection left idle in a transaction is then closed
  - and a `statement_timeout` for the web's role, so neither holds the watermark for long.

## Owning the log

```python
from isik.django.apps.sync.bases import AbstractSyncLogEntry

class SyncLogEntry(AbstractSyncLogEntry):
    class Meta:
        db_table = "sync_log"

ISIK = {"DJANGO": {"SYNC": {"LOG_MODEL": "sync.SyncLogEntry"}}}
```

Every concrete subclass gets the log's rules when it's prepared - append-only (`protect_sync_log_change`), no
`TRUNCATE` (`protect_sync_log_truncate`), and the `(xid, seq)` index (`<table>_position`) - and the triggers
write into it. `get_sync_log_model()` reads it.
