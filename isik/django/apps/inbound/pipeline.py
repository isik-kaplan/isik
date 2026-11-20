"""
What becomes of a received event: `record()` writes it down once, `process()` hands it to its handler - claimed,
then handled in a transaction of its own - and `work()` sends the due ones, takes back what a dead worker held, and
prunes what's past `RETENTION`. The row is the queue; a task is only a hint, and `work()` sends again what one lost.
"""

import logging
import random
from contextlib import contextmanager
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.inbound import conf
from isik.django.apps.inbound.choices import DONE, EventState, PayloadKept
from isik.django.apps.inbound.sources import handler_for, sources
from isik.django.apps.inbound.swapping import get_inbound_event_model


logger = logging.getLogger(__name__)


class RetryRefused(Exception):
    """Why an event can't be retried by hand - it isn't given up on."""


@contextmanager
def in_partition(key):
    """Enters partition `key` - `IN_PARTITION(key)` - or, with no partitions, nothing at all."""
    if not conf.partitioned():
        yield
        return
    with conf.setting("IN_PARTITION")(key):
        yield


def _events():
    return get_inbound_event_model()._default_manager


def record(source, event_id, event_type, payload, partition):
    """
    Writes down an event of `source` - in the partition the caller entered - and asks for it to be handled once the
    transaction commits. Returns the event, or None for a resend of one received already.
    """
    try:
        # A savepoint: a resend's conflict leaves the request's own transaction usable.
        with transaction.atomic():
            event = _events().create(
                source=source.name,
                event_id=event_id,
                event_type=event_type,
                partition=partition,
                payload=payload,
            )
    except IntegrityError:
        return None
    transaction.on_commit(lambda: _ask(event))
    return event


def _ask(event):
    from isik.django.apps.inbound import tasks

    tasks.process.delay(event.partition, str(event.pk))


def backoff(attempts):
    """How long to wait after the `attempts`-th failed attempt: `BACKOFF`'s entry for it, the last once they run
    out, plus up to a tenth more, so events that failed together aren't tried together."""
    delays = conf.setting("BACKOFF")
    delay = delays[min(attempts, len(delays)) - 1]
    return timedelta(seconds=delay + random.uniform(0, delay / 10))


def process(partition, event_id):
    """Hands the event `event_id` to its handler, in `partition`, if it's due and no worker holds it. Returns it."""
    with in_partition(partition):
        claimed = _claim(event_id)
        if claimed is None:
            return None
        handler = handler_for(claimed.source, claimed.event_type)
        if handler is None:
            return _finish(claimed, EventState.IGNORED)
        try:
            with transaction.atomic():
                handler(claimed)
                finished = _finish(claimed, EventState.PROCESSED)
                # Taken back while it ran: what the handler wrote goes too, as the worker now holding it will
                # write it again.
                if finished is None:
                    transaction.set_rollback(True)
                return finished
        except Exception as error:
            logger.exception("Inbound event %s failed in its handler.", claimed.pk)
            return _failed(claimed, error)


def _claim(event_id):
    now = timezone.now()
    with transaction.atomic():
        event = (
            _events()
            .select_for_update(skip_locked=True)
            .filter(pk=event_id, state=EventState.PENDING, next_attempt_at__lte=now)
            .first()
        )
        if event is None:
            return None
        event.state, event.claimed_at, event.attempt_count = EventState.PROCESSING, now, event.attempt_count + 1
        event.save(update_fields=["state", "claimed_at", "attempt_count"])
    return event


def _finish(event, state):
    """Marks the claimed `event` done in `state` - unless another worker took it back since, as a stale one's
    finish must not overwrite. Returns it, or None for a stale finish."""
    fields = {"state": state, "processed_at": timezone.now(), "last_error": ""}
    if state == EventState.PROCESSED and sources[event.source].payload_kept == PayloadKept.UNTIL_HANDLED:
        fields["payload"] = None
    return _update_claimed(event, fields)


def _failed(event, error):
    attempts = conf.setting("MAX_ATTEMPTS")
    fields = {"last_error": str(error) or type(error).__name__}
    if attempts is not None and event.attempt_count >= attempts:
        fields |= {"state": EventState.ABANDONED, "processed_at": timezone.now()}
    else:
        fields |= {"state": EventState.PENDING, "next_attempt_at": timezone.now() + backoff(event.attempt_count)}
    return _update_claimed(event, fields)


def _update_claimed(event, fields):
    moved = (
        _events()
        .filter(pk=event.pk, state=EventState.PROCESSING, claimed_at=event.claimed_at)
        .update(**fields, claimed_at=None)
    )
    if not moved:
        logger.warning("Inbound event %s was taken back while it was handled; its finish is dropped.", event.pk)
        return None
    for name, value in fields.items():
        setattr(event, name, value)
    event.claimed_at = None
    return event


def retry_event(event):
    """Moves the given-up-on `event` back to pending, due now, with its attempts counted from none again."""
    moved = (
        _events()
        .filter(pk=event.pk, state=EventState.ABANDONED)
        .update(state=EventState.PENDING, next_attempt_at=timezone.now(), attempt_count=0, processed_at=None)
    )
    if not moved:
        raise RetryRefused(_("Only an event given up on is retried by hand, and %(event)s isn't.") % {"event": event})
    transaction.on_commit(lambda: _ask(event))


def _partitions():
    return conf.setting("PARTITIONS")() if conf.partitioned() else [None]


def _limited(queryset):
    batch = conf.setting("BATCH")
    return queryset if batch is None else queryset[:batch]


def work():
    """
    In each partition: asks again for every due event, takes back events claimed longer than `STRANDED_AFTER` -
    a worker that died with them - and prunes done events older than `RETENTION`. Returns the counts.
    """
    done = {"asked": 0, "taken_back": 0, "pruned": 0}
    now = timezone.now()
    for partition in _partitions():
        with in_partition(partition):
            stranded = _limited(
                _events()
                .filter(state=EventState.PROCESSING, claimed_at__lt=now - conf.setting("STRANDED_AFTER"))
                .values_list("pk", flat=True)
            )
            done["taken_back"] += (
                _events()
                .filter(pk__in=list(stranded), state=EventState.PROCESSING)
                .update(state=EventState.PENDING, claimed_at=None, next_attempt_at=now)
            )
            due = list(
                _limited(
                    _events().filter(state=EventState.PENDING, next_attempt_at__lte=now).order_by("next_attempt_at")
                )
            )
            for event in due:
                _ask(event)
            done["asked"] += len(due)
            retention = conf.setting("RETENTION")
            if retention is not None:
                old = _limited(
                    _events().filter(state__in=DONE, received_at__lt=now - retention).values_list("pk", flat=True)
                )
                done["pruned"] += _events().filter(pk__in=list(old)).delete()[0]
    return done
