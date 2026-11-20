"""A received event handed to its handler: claimed, handled with the mark in one transaction, tried again on a
backoff, given up on, retried by hand - and the housekeeping work() does."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from isik.django.apps.inbound import pipeline, tasks
from isik.django.apps.inbound.choices import EventState
from isik.django.apps.inbound.models import InboundEvent
from isik.django.apps.inbound.sources import handlers
from isik.django.conf import isik_areas
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db

INBOUND = {"BACKOFF": (60, 300), "STRANDED_AFTER": timedelta(minutes=10)}


def received(source="acme", event_type="invoice_voided", event_id="evt_1", **fields):
    return InboundEvent.objects.create(
        source=source, event_id=event_id, event_type=event_type, payload={"id": event_id}, **fields
    )


def fresh(event):
    return InboundEvent.objects.get(pk=event.pk)


def inbound(settings, **given):
    isik_areas(settings).INBOUND = {**INBOUND, **given}


class TestHandled:
    def test_by_its_handler_and_marked(self, handled):
        event = received()

        done = pipeline.process(None, str(event.pk))

        assert handled == [("invoice", "evt_1")]
        event = fresh(event)
        assert done.state == event.state == EventState.PROCESSED
        assert (event.attempt_count, event.claimed_at, event.last_error) == (1, None, "")
        assert event.processed_at is not None
        assert event.payload == {"id": "evt_1"}

    def test_a_source_keeping_the_payload_until_then_drops_it(self, handled):
        event = received(source="pinger", event_type="ping", event_id=None)

        pipeline.process(None, str(event.pk))

        assert (fresh(event).state, fresh(event).payload) == (EventState.PROCESSED, None)

    def test_one_no_handler_takes_is_ignored(self, handled):
        event = received(event_type="customer_created")

        assert pipeline.process(None, str(event.pk)).state == EventState.IGNORED
        assert handled == []
        assert fresh(event).processed_at is not None

    def test_a_handler_for_every_type_takes_one_with_none_of_its_own(self, handled):
        event = received(source="pinger", event_type="pong", event_id=None)

        assert pipeline.process(None, str(event.pk)).state == EventState.PROCESSED
        assert handled == [("ping", event.pk)]

    def test_not_yet_due_is_left(self, handled):
        event = received(next_attempt_at=timezone.now() + timedelta(minutes=1))

        assert pipeline.process(None, str(event.pk)) is None
        assert handled == []

    @pytest.mark.parametrize("state", [EventState.PROCESSING, EventState.PROCESSED, EventState.ABANDONED])
    def test_one_not_pending_is_left(self, handled, state):
        event = received(state=state)

        assert pipeline.process(None, str(event.pk)) is None
        assert handled == []

    def test_only_the_event_named(self, handled):
        # The other first, so any pending event but the one named is the other.
        other, event = received(event_id="evt_2"), received()

        pipeline.process(None, str(event.pk))

        assert (fresh(event).state, fresh(other).state, fresh(other).attempt_count) == (
            EventState.PROCESSED,
            EventState.PENDING,
            0,
        )

    def test_claimed_while_its_handler_runs(self, monkeypatch):
        seen = []
        monkeypatch.setitem(
            handlers,
            ("acme", "invoice_voided"),
            lambda event: seen.append((event.claimed_at, fresh(event).state, fresh(event).claimed_at)),
        )
        event = received()

        pipeline.process(None, str(event.pk))

        ((claimed_at, state, stored),) = seen
        assert (state, stored) == (EventState.PROCESSING, claimed_at)
        assert claimed_at is not None

    def test_a_finish_marks_only_its_own_event(self, handled, monkeypatch):
        now = timezone.now()
        monkeypatch.setattr(pipeline.timezone, "now", lambda: now)
        other = received(event_id="evt_2", state=EventState.PROCESSING, claimed_at=now)
        event = received(next_attempt_at=now - timedelta(seconds=1))

        done = pipeline.process(None, str(event.pk))

        assert (done.state, done.claimed_at) == (EventState.PROCESSED, None)
        assert (fresh(other).state, fresh(other).claimed_at) == (EventState.PROCESSING, now)

    def test_what_the_handler_writes_commits_with_the_mark(self, monkeypatch):
        monkeypatch.setitem(handlers, ("acme", "invoice_voided"), lambda event: Widget.objects.create(name="paid"))
        event = received()

        pipeline.process(None, str(event.pk))

        assert Widget.objects.filter(name="paid").exists()

    def test_a_finish_after_it_was_taken_back_is_dropped_with_what_the_handler_wrote(self, monkeypatch):
        def taken_back(event):
            Widget.objects.create(name="twice")
            InboundEvent.objects.filter(pk=event.pk).update(claimed_at=timezone.now() + timedelta(seconds=1))

        monkeypatch.setitem(handlers, ("acme", "invoice_voided"), taken_back)
        event = received()

        with caplog_at_warning() as caplog:
            assert pipeline.process(None, str(event.pk)) is None
        assert not Widget.objects.filter(name="twice").exists()
        assert fresh(event).state == EventState.PROCESSING
        assert [record.getMessage() for record in caplog] == [
            f"Inbound event {event.pk} was taken back while it was handled; its finish is dropped."
        ]


class TestFailed:
    @pytest.fixture
    def failing(self, monkeypatch):
        def fail(event):
            Widget.objects.create(name="half")
            raise RuntimeError("the ledger is down")

        monkeypatch.setitem(handlers, ("acme", "invoice_voided"), fail)

    def test_tried_again_after_its_backoff(self, failing, monkeypatch):
        monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: high)
        event = received()
        before = timezone.now()

        done = pipeline.process(None, str(event.pk))

        event = fresh(event)
        assert done.state == event.state == EventState.PENDING
        assert (event.attempt_count, event.last_error, event.claimed_at) == (1, "the ledger is down", None)
        assert before + timedelta(seconds=66) <= event.next_attempt_at <= timezone.now() + timedelta(seconds=66)
        assert not Widget.objects.filter(name="half").exists()

    def test_each_failure_waits_the_next_delay_the_last_repeating(self, failing, monkeypatch):
        monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: low)
        event = received(attempt_count=2)

        pipeline.process(None, str(event.pk))

        waited = fresh(event).next_attempt_at - timezone.now()
        assert timedelta(seconds=290) < waited <= timedelta(seconds=300)
        assert pipeline.backoff(1) <= timedelta(seconds=66)
        assert pipeline.backoff(5) >= timedelta(seconds=300)

    def test_an_error_saying_nothing_is_named_by_its_type(self, monkeypatch):
        def fail(event):
            raise KeyError

        monkeypatch.setitem(handlers, ("acme", "invoice_voided"), fail)
        event = received()

        pipeline.process(None, str(event.pk))

        assert fresh(event).last_error == "KeyError"

    def test_given_up_on_after_max_attempts(self, failing, settings):
        inbound(settings, MAX_ATTEMPTS=2)
        event = received(attempt_count=1)

        done = pipeline.process(None, str(event.pk))

        assert done.state == fresh(event).state == EventState.ABANDONED
        assert fresh(event).processed_at is not None
        assert fresh(event).last_error == "the ledger is down"

    def test_logged_with_the_event(self, failing, caplog):
        event = received()

        pipeline.process(None, str(event.pk))

        (logged,) = [record for record in caplog.records if record.name == "isik.django.apps.inbound.pipeline"]
        assert logged.getMessage() == f"Inbound event {event.pk} failed in its handler."
        assert logged.exc_info is not None

    def test_tried_again_before_max_attempts(self, failing, settings):
        inbound(settings, MAX_ATTEMPTS=3)
        event = received(attempt_count=1)

        assert pipeline.process(None, str(event.pk)).state == EventState.PENDING


class TestRetriedByHand:
    def test_one_given_up_on_is_due_now_from_no_attempts(self, asked):
        other = received(event_id="evt_2", state=EventState.ABANDONED)
        event = received(
            state=EventState.ABANDONED,
            attempt_count=5,
            processed_at=timezone.now(),
            next_attempt_at=timezone.now() + timedelta(hours=1),
        )

        with TestCase.captureOnCommitCallbacks(execute=True):
            pipeline.retry_event(event)

        event = fresh(event)
        assert (event.state, event.attempt_count, event.processed_at) == (EventState.PENDING, 0, None)
        assert event.next_attempt_at <= timezone.now()
        assert asked == [(None, str(event.pk))]
        assert fresh(other).state == EventState.ABANDONED

    def test_one_not_given_up_on_is_refused(self):
        event = received(state=EventState.PROCESSED)

        with pytest.raises(pipeline.RetryRefused) as refused:
            pipeline.retry_event(event)

        assert str(refused.value) == f"Only an event given up on is retried by hand, and {event} isn't."


class TestWork:
    def test_takes_back_what_a_dead_worker_held(self, asked):
        stranded = received(state=EventState.PROCESSING, claimed_at=timezone.now() - timedelta(minutes=11))
        working = received(event_id="evt_2", state=EventState.PROCESSING, claimed_at=timezone.now())

        done = pipeline.work()

        assert (fresh(stranded).state, fresh(stranded).claimed_at) == (EventState.PENDING, None)
        assert fresh(working).state == EventState.PROCESSING
        assert done == {"asked": 1, "taken_back": 1, "pruned": 0}
        assert asked == [(None, str(stranded.pk))]

    def test_takes_back_only_whats_still_held(self, asked):
        abandoned = received(state=EventState.ABANDONED, claimed_at=timezone.now() - timedelta(hours=1))
        stranded = received(
            event_id="evt_2",
            state=EventState.PROCESSING,
            claimed_at=timezone.now() - timedelta(hours=1),
            next_attempt_at=timezone.now() + timedelta(days=1),
        )

        assert pipeline.work()["taken_back"] == 1
        assert fresh(abandoned).state == EventState.ABANDONED
        assert fresh(stranded).next_attempt_at <= timezone.now()

    def test_asks_again_for_every_due_event_oldest_first(self, asked):
        later = received(next_attempt_at=timezone.now() - timedelta(minutes=1))
        sooner = received(event_id="evt_2", next_attempt_at=timezone.now() - timedelta(minutes=2))
        received(event_id="evt_3", next_attempt_at=timezone.now() + timedelta(minutes=1))
        received(event_id="evt_4", state=EventState.PROCESSED)

        assert pipeline.work()["asked"] == 2
        assert asked == [(None, str(sooner.pk)), (None, str(later.pk))]

    def test_prunes_done_events_past_retention(self, settings, asked):
        inbound(settings, RETENTION=timedelta(days=30))
        old = timezone.now() - timedelta(days=31)
        for number, state in enumerate(EventState):
            received(
                event_id=f"old_{number}",
                state=state,
                received_at=old,
                next_attempt_at=timezone.now() + timedelta(days=1),
            )
        kept = received(event_id="new", state=EventState.PROCESSED)

        assert pipeline.work()["pruned"] == 3
        assert set(InboundEvent.objects.values_list("event_id", flat=True)) == {"old_0", "old_1", "new"}
        assert InboundEvent.objects.filter(pk=kept.pk).exists()

    def test_prunes_nothing_without_retention(self, asked):
        received(state=EventState.PROCESSED, received_at=timezone.now() - timedelta(days=3000))

        assert pipeline.work()["pruned"] == 0

    def test_a_batch_at_a_time(self, settings, asked):
        inbound(settings, BATCH=1, RETENTION=timedelta(days=1))
        for number in range(2):
            received(event_id=f"due_{number}", next_attempt_at=timezone.now() - timedelta(minutes=number))
            received(
                event_id=f"stranded_{number}",
                state=EventState.PROCESSING,
                claimed_at=timezone.now() - timedelta(hours=1),
                next_attempt_at=timezone.now() + timedelta(days=1),
            )
            received(
                event_id=f"old_{number}", state=EventState.PROCESSED, received_at=timezone.now() - timedelta(days=2)
            )

        assert pipeline.work() == {"asked": 1, "taken_back": 1, "pruned": 1}


def _entering(entered):
    @contextmanager
    def in_partition(key):
        entered.append(key)
        yield

    return in_partition


@contextmanager
def caplog_at_warning():
    import logging

    records = []
    handler = logging.Handler(logging.WARNING)
    handler.emit = records.append
    logger = logging.getLogger("isik.django.apps.inbound.pipeline")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


class TestPartitions:
    @pytest.fixture
    def partitioned(self, settings):
        entered = []
        inbound(settings, PARTITIONS=lambda: ["north", "south"], IN_PARTITION=_entering(entered))
        return entered

    def test_the_task_handles_it_in_the_partition_it_names(self, partitioned, handled):
        event = received(partition="south")

        assert tasks.process("south", str(event.pk)) == EventState.PROCESSED
        assert partitioned == ["south"]

    def test_an_event_is_handled_in_its_partition(self, partitioned, handled):
        event = received(partition="north")

        pipeline.process("north", str(event.pk))

        assert partitioned == ["north"]
        assert handled == [("invoice", "evt_1")]

    def test_work_goes_through_each_partition(self, partitioned, asked):
        assert pipeline.work() == {"asked": 0, "taken_back": 0, "pruned": 0}
        assert partitioned == ["north", "south"]

    def test_work_adds_up_every_partitions(self, partitioned, asked, settings):
        # The fake partitions share one table: what the first takes back, prunes or asks for is counted with
        # what the second does.
        inbound(
            settings,
            PARTITIONS=lambda: ["north", "south"],
            IN_PARTITION=_entering(partitioned),
            RETENTION=timedelta(days=1),
        )
        received(state=EventState.PROCESSING, claimed_at=timezone.now() - timedelta(hours=1))
        received(event_id="evt_2", state=EventState.PROCESSED, received_at=timezone.now() - timedelta(days=2))

        assert pipeline.work() == {"asked": 2, "taken_back": 1, "pruned": 1}

    def test_with_none_nothing_is_entered(self, handled):
        event = received()

        with pipeline.in_partition("anything"):
            assert pipeline.process(None, str(event.pk)).state == EventState.PROCESSED


class TestTasks:
    def test_process_answers_the_events_state(self, handled):
        event = received()

        assert tasks.process(None, str(event.pk)) == EventState.PROCESSED
        assert tasks.process(None, str(event.pk)) is None

    def test_work(self, asked):
        assert tasks.work() == {"asked": 0, "taken_back": 0, "pruned": 0}

    def test_the_beat_entry(self):
        assert tasks.beat_schedule(60) == {"isik.inbound.work": {"task": "isik.inbound.work", "schedule": 60}}


def test_a_db_round_trip_for_an_event_naming_its_source_and_id():
    event = received()

    assert str(event) == "acme:invoice_voided:evt_1"
    assert str(received(source="pinger", event_type="ping", event_id=None)).startswith("pinger:ping:")
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM isik_inbound_event")
        assert cursor.fetchone() == (2,)


@pytest.mark.django_db(transaction=True)
def test_an_event_another_worker_holds_is_skipped(handled):
    import threading

    from django.db import connection as default
    from django.db import transaction

    event = received()
    held, release = threading.Event(), threading.Event()

    def hold():
        with transaction.atomic():
            InboundEvent.objects.select_for_update().get(pk=event.pk)
            held.set()
            release.wait(5)
        default.close()

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    try:
        assert pipeline.process(None, str(event.pk)) is None
    finally:
        release.set()
        holder.join(5)
    assert pipeline.process(None, str(event.pk)).state == EventState.PROCESSED
