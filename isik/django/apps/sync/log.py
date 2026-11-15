"""
Reading the sync log in order: `watermark()`, the position below which every entry is final; `horizon()`, where
pruning cut it; `prune_sync_log()`; and `sync_feed_lag()`, how long an open transaction is holding the feed back.

An entry's position is (`xid`, `seq`). Transactions commit out of order, so an entry of a transaction still open
may yet appear below one already served - a cursor past it would never see it. The feed serves only entries below
the watermark: the lowest transaction id still open in this database, which only rises. Every transaction below
it has ended, its entries are all written, and none can be added there.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

import pgtrigger
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from isik.django.apps.sync import conf
from isik.django.apps.sync.choices import Operation
from isik.django.apps.sync.swapping import get_sync_log_model


logger = logging.getLogger(__name__)

# The lowest id among the transactions of this database still open - read off the snapshot, which lists every
# open transaction of the cluster, leaving out those pg_stat_activity places in another database, which can't
# write this database's log - else the snapshot's xmax, the next id to be given. A transaction pg_stat_activity
# doesn't show is kept: being wrong that way only waits longer.
WATERMARK = """
SELECT LEAST(
    pg_snapshot_xmax(snapshot)::text::bigint,
    (
        SELECT min(open::text::bigint) FROM pg_snapshot_xip(snapshot) AS open
        WHERE open::text::bigint & 4294967295 NOT IN (
            SELECT backend_xid::text::bigint FROM pg_stat_activity
            WHERE backend_xid IS NOT NULL AND datid IS DISTINCT FROM (
                SELECT oid FROM pg_database WHERE datname = current_database()
            )
        )
    )
) FROM pg_current_snapshot() AS snapshot
"""

# The oldest transaction of this database holding an id, other than the one asking: what holds the watermark.
OLDEST_OPEN = """
SELECT pid, xact_start FROM pg_stat_activity
WHERE datname = current_database() AND backend_xid IS NOT NULL AND pid <> pg_backend_pid()
ORDER BY xact_start LIMIT 1
"""


@dataclass(frozen=True, order=True)
class Position:
    """A place in the log: (`xid`, `seq`). `Position(xid, 0)` is before every entry of transaction `xid`."""

    xid: int
    seq: int

    def after(self):
        """The filter for the entries after this position."""
        return Q(xid__gt=self.xid) | Q(xid=self.xid, seq__gt=self.seq)

    @classmethod
    def of(cls, entry):
        """An entry's position."""
        return cls(entry.xid, entry.seq)


def watermark():
    """
    The transaction id below which every entry is final - a statement of its own, before any read of the log, so
    a transaction that ended before it is visible to the reads after it. Read stats are cleared first, as a
    transaction keeps the first it read.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(WATERMARK)
        return cursor.fetchone()[0]


def horizon():
    """The position pruning last cut the log at - None while nothing is pruned."""
    mark = get_sync_log_model()._default_manager.filter(op=Operation.PRUNED).order_by("-xid", "-seq").first()
    return None if mark is None else Position.of(mark)


def last_position(below):
    """The position of the last entry of a transaction below `below` - None for an empty log."""
    last = get_sync_log_model()._default_manager.filter(xid__lt=below).order_by("-xid", "-seq").first()
    return None if last is None else Position.of(last)


def _protections(model):
    return [f"{model._meta.label}:protect_sync_log_change"]


def prune_sync_log(batch=None):
    """
    Deletes the entries older than `RETENTION` - none when it's None - up to `batch` of them, every one when it's
    None, and marks where it cut: a cursor before the mark is answered 410. Only entries of transactions below the
    watermark go - a transaction still open may yet write before them. Returns how many entries it deleted.
    """
    retention = conf.setting("RETENTION")
    if retention is None:
        return 0
    log = get_sync_log_model()
    entries = log._default_manager
    below = watermark()
    with transaction.atomic():
        old = entries.filter(xid__lt=below, created_at__lt=timezone.now() - retention).exclude(op=Operation.PRUNED)
        if batch is None:
            last = old.order_by("-xid", "-seq").values_list("xid", "seq").first()
        else:
            last = ([None] + list(old.order_by("xid", "seq").values_list("xid", "seq")[:batch]))[-1]
        if last is None:
            return 0
        mark = Position(*last)
        with pgtrigger.ignore(*_protections(log)):
            # Every entry up to the cut - with ones not yet old, in a transaction older than one that is - and
            # the last mark, which this one replaces.
            deleted, _ = entries.filter(~mark.after()).exclude(op=Operation.PRUNED).delete()
            entries.filter(op=Operation.PRUNED).delete()
            entries.create(xid=mark.xid, seq=mark.seq, op=Operation.PRUNED)
    return deleted


@dataclass(frozen=True)
class FeedLag:
    """How long the oldest open transaction of this database has held the feed back, and its process id."""

    lag: timedelta
    pid: int


def sync_feed_lag():
    """
    How long the oldest transaction of this database still open - one holding an id, that is, one that has
    written - has been open: the feed serves nothing it wrote, nor anything after it, until it ends. None when
    there is none. For a health check: an idle-in-transaction connection stalls every person's feed.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(OLDEST_OPEN)
        row = cursor.fetchone()
    if row is None:
        return None
    pid, started = row
    return FeedLag(lag=timezone.now() - started, pid=pid)


def warn_of_lag():
    """Logs a warning naming the process holding the feed back past `LAG_WARNING` - nothing when it's None."""
    limit = conf.setting("LAG_WARNING")
    if limit is None:
        return
    held = sync_feed_lag()
    if held is not None and held.lag > limit:
        logger.warning(
            "The sync feed is held back %s by process %s, a transaction still open - see "
            "idle_in_transaction_session_timeout.",
            held.lag,
            held.pid,
        )
