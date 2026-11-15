"""
The feed: one page of a person's changes after a cursor - `Feed(request, cursor, limit, snapshots).page()`.

A cursor is opaque to the client and signed here: the log position the person has been served up to, the
snapshot being served, if one is, with the last row of it served, and the model snapshots asked for and not yet
begun. A missing cursor starts a full snapshot of every row the person sees, read at the watermark - the feed
then goes on from there. A scope granted starts a snapshot of the scope; `snapshot=<model>` one of the model.
Snapshots are served whole before the log goes on, so no live change interleaves with one.
"""

from collections import OrderedDict

from django.core import signing
from django.db.models import Q

from isik.django.apps.sync.choices import ROW_OPERATIONS, SCOPE_OPERATIONS, Operation
from isik.django.apps.sync.declarations import scopes_of, synced_models
from isik.django.apps.sync.log import IN_ORDER, Position, horizon, last_position, warn_of_lag, watermark
from isik.django.apps.sync.reading import Reader
from isik.django.apps.sync.swapping import get_sync_log_model


CURSOR_SALT = "isik.django.apps.sync.cursor"


class CursorExpired(Exception):
    """The cursor is before what pruning removed - or isn't one this server made."""


def encode_cursor(state):
    """A cursor holding `state`, signed."""
    return signing.dumps(state, salt=CURSOR_SALT)


def decode_cursor(cursor):
    """The state a cursor holds - `CursorExpired` for one not signed here."""
    try:
        return signing.loads(cursor, salt=CURSOR_SALT)
    except signing.BadSignature:
        raise CursorExpired() from None


def framing(op, snapshot):
    """A snapshot's `snapshot_begin` or `snapshot_end`."""
    return {"op": op, "scope": snapshot["scope"], "model": snapshot["model"]}


def _snapshot(scope=None, model=None):
    return {"scope": scope, "model": model, "after": None}


class Feed:
    """One page of `request`'s person's feed - see the module."""

    def __init__(self, request, cursor, limit, snapshots):
        self.request = request
        self.user = request.user
        self.scopes = scopes_of(self.user)
        self.synced = synced_models()
        self.cursor = cursor
        # How many more row changes this page carries - None for no end.
        self.budget = limit
        self.snapshots = snapshots
        self.changes = []
        self.readers = {}

    def reader(self, name):
        if name not in self.readers:
            self.readers[name] = Reader(self.synced[name], self.request, self.scopes)
        return self.readers[name]

    def _spend(self, count):
        if self.budget is not None:
            self.budget -= count

    def page(self):
        """`{changes, cursor, has_more}`."""
        self.below = watermark()
        warn_of_lag()
        if self.cursor is None:
            start = Position.before(self.below)
            state = {"p": [start.xid, start.seq], "open": _snapshot(), "queue": []}
            self.changes.append(framing("snapshot_begin", state["open"]))
        else:
            state = decode_cursor(self.cursor)
            cut = horizon()
            if cut is not None and Position(*state["p"]) < cut:
                raise CursorExpired()
        self.state = state
        for name in self.snapshots:
            pending = state["queue"] + ([state["open"]["model"]] if state["open"] else [])
            if name in self.synced and name not in pending:
                state["queue"].append(name)
        has_more = self._serve()
        return {"changes": self.changes, "cursor": encode_cursor(state), "has_more": has_more}

    def _serve(self):
        state = self.state
        while True:
            if state["open"] is not None:
                if self._serve_snapshot():
                    return True
                continue
            if state["queue"]:
                state["open"] = _snapshot(model=state["queue"].pop(0))
                self.changes.append(framing("snapshot_begin", state["open"]))
                continue
            stopped = self._scan()
            if stopped is None:
                return False
            if stopped is not True:
                state["open"] = _snapshot(scope=stopped)
                self.changes.append(framing("snapshot_begin", state["open"]))
                continue
            return True

    def _serve_snapshot(self):
        """Serves the open snapshot as far as the page goes - True when rows of it are left for the next."""
        snapshot = self.state["open"]
        scope = snapshot["scope"]
        if scope is not None and scope not in self.scopes:
            # Revoked while it was served: closed with no end - the scope_revoked the log holds ends it.
            self.state["open"] = None
            return False
        after_name, after_id = snapshot["after"] or (None, None)
        for name, synced in self.synced.items():
            prefix = f"{synced.kind}:"
            if snapshot["model"] not in (None, name) or (scope is not None and not scope.startswith(prefix)):
                continue
            if after_name is not None and name < after_name:
                continue
            reader = self.reader(name)
            scope_ids = reader.scope_ids if scope is None else [scope.removeprefix(prefix)]
            found, more = reader.page(after_id if name == after_name else None, self.budget, scope_ids)
            self.changes.extend(found)
            self._spend(len(found))
            if found:
                snapshot["after"] = [name, found[-1]["id"]]
            if more:
                return True
        self.changes.append(framing("snapshot_end", snapshot))
        self.state["open"] = None
        return False

    def _visible(self):
        rows = Q(op__in=ROW_OPERATIONS) & (Q(scope__in=self.scopes) | Q(old_scope__in=self.scopes))
        # Only a scope's entries name a member.
        return rows | Q(member=str(self.user.pk))

    def _scan(self):
        """
        Serves the log after the cursor, below the watermark, as far as the page goes: True when it stopped with
        more to serve, the scope a grant it reached opens a snapshot of, or None at the end.
        """
        position = Position(*self.state["p"])
        entries = get_sync_log_model()._default_manager.filter(self._visible(), position.after(), xid__lt=self.below)
        # {("row", model, id): None} and {("scope", seq): change}, in the order they're served - a row at its
        # last change.
        served = OrderedDict()
        rows = 0
        stopped = None
        for entry in entries.order_by(*IN_ORDER).iterator():
            if entry.op in SCOPE_OPERATIONS:
                served[("scope", entry.seq)] = {"op": entry.op, "scope": entry.scope}
                position = Position.of(entry)
                if entry.op == Operation.SCOPE_GRANTED:
                    stopped = entry.scope
                    break
                continue
            key = ("row", entry.model, entry.object_id)
            if key in served:
                served.move_to_end(key)
            elif entry.model in self.synced:
                if rows == self.budget:
                    stopped = True
                    break
                served[key] = None
                rows += 1
            position = Position.of(entry)
        if stopped is None:
            # The end: on to the last entry below the watermark, those of scopes the person isn't in too.
            position = max(position, last_position(self.below) or position)
        self.state["p"] = [position.xid, position.seq]
        self._emit(served)
        self._spend(rows)
        return stopped

    def _emit(self, served):
        ids = {}
        for key in served:
            if key[0] == "row":
                ids.setdefault(key[1], []).append(key[2])
        current = {name: self.reader(name).changes(each) for name, each in ids.items()}
        for key, change in served.items():
            if key[0] == "scope":
                self.changes.append(change)
                continue
            _, name, object_id = key
            self.changes.append(current[name].get(object_id) or {"op": "delete", "model": name, "id": object_id})
