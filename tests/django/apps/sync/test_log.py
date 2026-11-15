"""Reading the log in order - the watermark against real concurrent transactions, pruning, and the feed's lag."""

import logging
from datetime import timedelta

import pytest
from django.db import connection, connections

from isik.django.apps.sync import log
from isik.django.apps.sync.choices import Operation
from isik.django.apps.sync.log import (
    FeedLag,
    Position,
    horizon,
    last_position,
    prune_sync_log,
    sync_feed_lag,
    warn_of_lag,
    watermark,
)
from isik.django.apps.sync.swapping import get_sync_log_model
from isik.django.conf import override_isik
from tests.django.apps.sync import support


ALICE = "0190a000-0000-7000-8000-a00000000001"


def task(number):
    return {"id": f"0190a000-0000-7000-8000-1000000000{number:02}", "owner": ALICE, "title": "t", "done": False}


@pytest.fixture
def other():
    """A second connection to the database - another process's transaction."""
    opened = connection.copy()
    opened.ensure_connection()
    opened.set_autocommit(False)
    yield opened
    opened.rollback()
    opened.close()


def open_write(opened, number):
    """Writes a task in `opened`'s transaction, left open - returning its transaction id."""
    with opened.cursor() as cursor:
        support.user(ALICE)
        cursor.execute(
            "INSERT INTO testapp_synctask (id, owner_id, title, done, created_at, updated_at) "
            "VALUES (%s, %s, 't', false, now(), now())",
            [task(number)["id"], ALICE],
        )
        cursor.execute("SELECT pg_current_xact_id()::text::bigint")
        return cursor.fetchone()[0]


def entries_below(mark):
    return list(get_sync_log_model().objects.filter(xid__lt=mark).values_list("object_id", flat=True))


class TestAPosition:
    def test_orders_by_transaction_then_sequence(self):
        assert Position(1, 9) < Position(2, 0) < Position(2, 1)

    def test_after_it_is_every_later_entry(self):
        assert str(Position(3, 4).after()) == "(OR: ('xid__gt', 3), (AND: ('seq__gt', 4), ('xid', 3)))"


@pytest.mark.django_db(transaction=True)
class TestTheWatermark:
    def test_waits_for_a_transaction_still_open_that_wrote_first(self, other):
        held = open_write(other, 1)
        support.create("tasks", task(2))

        mark = watermark()
        assert mark == held
        assert entries_below(mark) == []

        other.commit()
        assert sorted(entries_below(watermark())) == [task(1)["id"], task(2)["id"]]

    def test_a_transaction_that_ends_lets_every_entry_through(self, other):
        open_write(other, 1)
        other.rollback()
        support.create("tasks", task(2))

        assert entries_below(watermark()) == [task(2)["id"]]

    def test_a_transaction_of_another_database_holds_nothing(self):
        elsewhere = connections["default"].copy()
        elsewhere.settings_dict = {**elsewhere.settings_dict, "NAME": "postgres"}
        elsewhere.ensure_connection()
        elsewhere.set_autocommit(False)
        try:
            with elsewhere.cursor() as cursor:
                cursor.execute("SELECT pg_current_xact_id()::text::bigint")
                held = cursor.fetchone()[0]
            support.create("tasks", task(2))

            assert watermark() > held
            assert entries_below(watermark()) == [task(2)["id"]]
        finally:
            elsewhere.rollback()
            elsewhere.close()

    def test_the_last_position_is_of_the_last_entry_below_it(self):
        assert last_position(watermark()) is None
        support.create("tasks", task(1))
        entry = get_sync_log_model().objects.get()

        assert last_position(watermark()) == Position(entry.xid, entry.seq)
        assert last_position(entry.xid) is None


@pytest.mark.django_db(transaction=True)
class TestPruning:
    def prune(self, retention, **kwargs):
        with override_isik(DJANGO={"SYNC": {"RETENTION": retention}}):
            return prune_sync_log(**kwargs)

    def test_with_no_retention_keeps_everything(self):
        support.create("tasks", task(1))

        assert self.prune(None) == 0
        assert horizon() is None

    def test_deletes_what_s_older_and_marks_the_cut(self):
        for number in range(3):
            support.create("tasks", task(number))
        last = get_sync_log_model().objects.order_by("seq").last()

        assert self.prune(timedelta(microseconds=1)) == 3
        assert list(get_sync_log_model().objects.values_list("op", "xid", "seq")) == [
            (Operation.PRUNED, last.xid, last.seq)
        ]
        assert horizon() == Position(last.xid, last.seq)

    def test_a_batch_at_a_time_the_mark_moving_on(self):
        for number in range(3):
            support.create("tasks", task(number))
        first, second, third = get_sync_log_model().objects.order_by("seq")

        assert self.prune(timedelta(microseconds=1), batch=2) == 2
        assert horizon() == Position(second.xid, second.seq)
        assert self.prune(timedelta(microseconds=1), batch=2) == 1
        assert horizon() == Position(third.xid, third.seq)
        assert get_sync_log_model().objects.count() == 1

    def test_nothing_old_enough_is_nothing_pruned(self):
        support.create("tasks", task(1))

        assert self.prune(timedelta(days=1)) == 0
        assert self.prune(timedelta(days=1), batch=5) == 0
        assert horizon() is None

    def test_a_transaction_still_open_and_all_after_it_stay(self, other):
        open_write(other, 1)
        support.create("tasks", task(2))

        assert self.prune(timedelta(microseconds=1)) == 0


@pytest.mark.django_db(transaction=True)
class TestTheLag:
    def test_none_while_no_transaction_is_open(self):
        assert sync_feed_lag() is None

    def test_the_oldest_open_transaction_and_its_process(self, other):
        open_write(other, 1)
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid = cursor.fetchone()[0]

        held = sync_feed_lag()
        assert held.pid == pid
        assert timedelta(0) < held.lag < timedelta(minutes=1)

    def test_warned_of_past_lag_warning(self, other, caplog, monkeypatch):
        monkeypatch.setattr(log, "sync_feed_lag", lambda: FeedLag(lag=timedelta(seconds=90), pid=7))
        with caplog.at_level(logging.WARNING, logger="isik.django.apps.sync.log"):
            warn_of_lag()
            with override_isik(DJANGO={"SYNC": {"LAG_WARNING": timedelta(seconds=90)}}):
                warn_of_lag()
            with override_isik(DJANGO={"SYNC": {"LAG_WARNING": timedelta(seconds=60)}}):
                warn_of_lag()

        assert caplog.messages == [
            "The sync feed is held back 0:01:30 by process 7, a transaction still open - see "
            "idle_in_transaction_session_timeout."
        ]

    def test_not_warned_with_nothing_open(self, caplog):
        with caplog.at_level(logging.WARNING), override_isik(DJANGO={"SYNC": {"LAG_WARNING": timedelta(seconds=1)}}):
            warn_of_lag()

        assert caplog.messages == []


@pytest.mark.django_db(transaction=True)
class TestInterleavedTransactions:
    """A transaction writing before and after another's commit: positions order by transaction, then sequence."""

    @pytest.fixture
    def interleaved(self, other):
        """(A, 1), (B, 2), (B, 3), (A, 4) by sequence - A the older transaction, committed last."""
        open_write(other, 1)
        support.create_many("tasks", [task(2), task(3)])
        with other.cursor() as cursor:
            cursor.execute(
                "INSERT INTO testapp_synctask (id, owner_id, title, done, created_at, updated_at) "
                "VALUES (%s, %s, 't', false, now(), now())",
                [task(4)["id"], ALICE],
            )
        other.commit()
        return list(get_sync_log_model().objects.order_by("seq").values_list("object_id", "xid", "seq"))

    def test_the_last_position_is_the_younger_transaction_s_last(self, interleaved):
        [_, _, (_, younger, last), _] = interleaved

        assert last_position(watermark()) == Position(younger, last)

    def test_pruning_a_batch_cuts_after_the_older_transaction(self, interleaved):
        [(_, older, _), _, _, (_, _, its_last)] = interleaved
        with override_isik(DJANGO={"SYNC": {"RETENTION": timedelta(microseconds=1)}}):
            assert prune_sync_log(batch=2) == 2

        assert horizon() == Position(older, its_last)

    def test_pruning_everything_cuts_after_the_younger(self, interleaved):
        [_, _, (_, younger, last), _] = interleaved
        with override_isik(DJANGO={"SYNC": {"RETENTION": timedelta(microseconds=1)}}):
            assert prune_sync_log() == 4

        assert horizon() == Position(younger, last)


@pytest.mark.django_db(transaction=True)
def test_pruning_with_nothing_new_leaves_the_mark_as_it_is():
    support.create("tasks", task(1))
    with override_isik(DJANGO={"SYNC": {"RETENTION": timedelta(microseconds=1)}}):
        prune_sync_log()
        mark = get_sync_log_model().objects.get()

        assert prune_sync_log() == 0
        assert prune_sync_log(batch=1) == 0

    assert get_sync_log_model().objects.get().pk == mark.pk
