# Isik sync protocol, version 1

The wire contract between a server that serves offline sync (isik) and a client that keeps a local
copy (`@isikk/core/sync`). Each side pins a copy of this directory. Neither side's tests run the
other: both run the fixtures in `fixtures/`, and every message validates against `schema.json`.

A change to anything here is a new protocol version, in a new directory.

## Versioning

- Every request to `/sync/capabilities`, `/sync/changes` and `/sync/push` carries
  `Isik-Sync-Protocol: 1`, and every response to one echoes it.
- A request with a missing or unsupported version gets
  `400 { "code": "sync_protocol_unsupported", "supported": [1] }`. A client that gets it stops
  syncing and asks to be updated; it retries nothing.

## Resources and scopes

A syncable resource is described in the OpenAPI document by an `x-sync` extension on its component
schema:

```json
{
  "protocol": 1,
  "model": "tasks",
  "path": "/api/v0/tasks/",
  "primary_key": "id",
  "scope": { "kind": "user", "field": "owner" },
  "columns": { "id": "uuid", "owner": "uuid", "title": "string", "done": "boolean", "project": "uuid" },
  "indexed": [["owner"], ["project"], ["done", "title"]],
  "hash": "9f2c..."
}
```

- `model` is the name the feed and push use. `path` is the REST collection path; the detail path is
  `{path}{id}/`.
- `columns` names every field of the REST representation, as one of `string`, `integer`, `number`,
  `boolean`, `uuid`, `datetime`, `date`, `json`.
- `hash` is computed by the server over the rest of the object. A client only compares it with the
  one it last saw.
- A **scope** is a string `"<kind>:<id>"`: `"user:0190…"`, `"tenant:0190…"`. A row's scope is
  `` `${scope.kind}:${data[scope.field]}` ``. Every `data` carries `scope.field`.

## Capabilities: `GET /sync/capabilities`

`200 { "protocol": 1, "push": { "backend": "<name>", "max_entries": n | null, "max_bytes": n | null } }`

- The server advertises its push limits so no client hardcodes them; `null` is no server limit.
  `backend` names the server's push backend, for diagnostics only - every backend answers with the
  same `results` shape.
- A client reads it before its first push and again after a `413`.

## Pull: `GET /sync/changes`

Query: `cursor` (opaque, absent on first sync), `limit` (a request; the server may serve fewer),
`snapshot` (a model name, repeatable - see "Snapshots").

`200 { "changes": [Change], "cursor": string, "has_more": boolean }`, with an `ETag`.

A `Change` is one of:

| `op`             | fields                                     | meaning                                 |
| ---------------- | ------------------------------------------ | --------------------------------------- |
| `upsert`         | `model`, `id`, `etag`, `data`              | the row as REST `retrieve` shows it     |
| `delete`         | `model`, `id`                              | gone, or no longer visible to this user |
| `scope_granted`  | `scope`                                    | informational; a snapshot of it follows |
| `scope_revoked`  | `scope`                                    | drop every local row of that scope      |
| `snapshot_begin` | `scope`, `model` (each a string or `null`) | rows that follow are a snapshot         |
| `snapshot_end`   | `scope`, `model`                           | that snapshot is complete               |

- `etag` is exactly the `ETag` header REST sends for the row, quotes included, and `data` and `etag`
  come from one read at serve time.
- A row the read cannot find, or the user can no longer see, arrives as `delete`, whatever the log
  recorded. A row moved between scopes reaches a user who sees only the old scope as `delete`, one
  who sees only the new scope as `upsert`, and one who sees both as both, in that order.
- Within a page, several changes to one row collapse to the latest. `cursor` is the highest position
  scanned, not the last one emitted, so a page may hold no changes and still advance it.
- `limit` counts `upsert` and `delete` only. Scope and snapshot events ride along uncounted, though a
  server may bound them per page by its own configuration; a client accepts a page whatever its event
  count.
- A cursor older than the server's tombstone retention gets
  `410 { "code": "sync_cursor_expired" }`. The client drops its cursor and pulls without one.
- Re-requesting the same cursor with `If-None-Match` set to that response's `ETag` gets `304` when
  nothing changed.
- `401` and `403` pause the client's pulling. They never wipe anything.

### Snapshots

A missing cursor, a granted scope and a requested model are each served by a snapshot: the rows read
at a recorded watermark, framed in the feed, after which the feed continues from that watermark. A
snapshot is never a replay of the log.

| trigger                     | framing                                       | covers                             |
| --------------------------- | --------------------------------------------- | ---------------------------------- |
| no cursor (first sync, 410) | `snapshot_begin { scope: null, model: null }` | every row the user sees            |
| `scope_granted S`           | `snapshot_begin { scope: S, model: null }`    | every row of scope S               |
| `snapshot=M` on the request | `snapshot_begin { scope: null, model: M }`    | every row of model M the user sees |

- Between a `snapshot_begin` and its `snapshot_end`, every change the snapshot covers is an `upsert`
  belonging to the snapshot, and the server emits no live change it covers. The client stages them
  and swaps them in at `snapshot_end`: a covered local row absent from the snapshot is gone.
- A snapshot may span pages; the cursor resumes inside it. `snapshot_end` comes in the page that
  holds the snapshot's last row, or with `snapshot_begin` when the snapshot is empty.
- A full snapshot is never open beside another. Scope and model snapshots may be open together.
- `scope_revoked S` while a snapshot of S is open ends it without a `snapshot_end`; the client
  discards its staging.
- A client sends `snapshot=M` on each request until it sees `snapshot_begin { model: M }`; a server
  ignores a repeat for a model whose snapshot is already pending or open.
- Inside a snapshot, rows come ordered by model name, then by primary key ascending (a UUID compared
  as its lowercase string).

## Push: `POST /sync/push`

```json
{ "entries": [{ "key": "…", "method": "POST", "model": "tasks", "id": "…", "body": {}, "if_match": "\"…\"" }] }
```

- `key` is the entry's `Idempotency-Key` and `if_match` its `If-Match`. `method` is `POST`, `PATCH`,
  `PUT` or `DELETE`; `body` is absent on `DELETE`.
- `POST` creates with the client's `id` (a UUID; uuid7 recommended). A real collision is `409`.
- The server resolves `model` through its own registry - never a path from the client - and runs
  each entry through the resource's normal REST view, serializer and permissions.

`200 { "results": [Result] }`, one per entry, in entry order:

```json
{ "key": "…", "status": 201, "etag": "\"…\"", "data": {} }
```

- `status` is the entry's REST status. `data` and `etag` come with a `2xx` (none with `204`),
  `current` and `etag` (the current version's) with a `412`, `errors` (the REST error body) with a
  `400`, and the error body under `errors` with any other `4xx`.
- Entries run in order, each in its own transaction, committing one by one. **A backend commits
  nothing for an entry whose result is not `2xx`** - not the write, not its idempotency claim - whether
  the view raised or returned the error. A later entry still runs: one that names a row an earlier
  entry failed to create gets that failure (`400`) like any other.
- A crash in one entry is that entry's `500`; the batch continues.
- A replayed key returns the stored result. A key the server never committed runs afresh. A client
  therefore keeps an entry's key across uncertain outcomes (network failure, a non-`200` push
  response) and may mint a new one after any definite non-`2xx` result, since that committed
  nothing.
- An entry naming a model that is not syncable gets `404 { "code": "sync_model_unknown" }`.

Whole-request answers:

- `413 { "code": "sync_push_too_large", "max_entries": n | null, "max_bytes": n | null }` - the
  limits the capabilities document advertises; the client splits to fit and resends.
- `429` with `Retry-After` - from push's own throttle scope, which counts entries.
- `401` pauses the client's pushing; `400 sync_protocol_unsupported` stops it.

- How a server runs the entries is its push backend's business (isik's default runs each through its
  REST view); the request and `results` shapes above are the same for every backend.
- Batch sizes are configuration on both sides, never protocol constants: the server's limits are
  whatever the project set (`null` when unset), and a client sends at most the smaller of its own
  setting and the server's.

The per-entry REST endpoints stay valid with `Idempotency-Key` and `If-Match`; push is the same writes
in one request, and a client may replay its outbox through them instead.

## Fixtures

`fixtures/*.json`, each validating against `schema.json#/$defs/Fixture`:

```json
{
  "name": "first-sync-paging",
  "description": "…",
  "given": { "user": "1", "memberships": ["team:7"], "rows": { "tasks": [{ "id": "…", "owner": 1 }] } },
  "steps": [
    {
      "request": { "method": "GET", "path": "/sync/changes", "query": { "limit": "2" } },
      "response": { "status": 200, "body": { "changes": ["…"], "cursor": "$cursor:1", "has_more": true } }
    },
    { "act": { "update": { "model": "tasks", "id": "…", "set": { "title": "b" } } } }
  ]
}
```

- `given` sets the world up before the first step: the requesting user, the scopes it is a member of
  (its own `user:<id>` scope always), and rows per model. `models.json` holds the `x-sync`
  of every model the fixtures use.
- An `act` step changes the world as another actor would, between requests: `create`, `update`
  (`set` may move a row's scope), `delete`, `grant` / `revoke` (a scope for the requesting user),
  `expire` (prune everything, so every cursor issued so far is past retention).
- A `request` step is matched against its `response`:
  - Every header the fixture names must be present with that value; other headers are free.
  - A string `"$<kind>:<name>"` is a placeholder: the first occurrence binds it, and every later one
    must equal it. Two different names of kind `etag` must bind different values; `cursor` names may
    bind equal values. `"$any"` matches anything. A placeholder in a request is replaced by its bound
    value.
  - Objects match key for key: a key the fixture lacks must be absent. Arrays match in order.
  - `given.push` sets the server's push limits for the fixture (`max_entries`, `max_bytes`, each
    `null` when absent), and `given.user: null` means an unauthenticated request.
