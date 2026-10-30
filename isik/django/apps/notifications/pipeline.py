"""
The work behind the tasks - fanning notices out into deliveries, sending them, retrying one by hand, taking
back what a dead worker left, and pruning - as plain functions, so they run (and are tested) without a
broker. The row is always the queue: a task only says something is due, and losing one makes it late,
never lost.

A worker claims a delivery or shard by stamping its `claimed_at`, and finishes it only while that stamp is
still its own - so one that ran past `STRANDED_AFTER`, and was taken back and claimed again, can't write
over the claim that replaced it.
"""

import functools
import logging
import random
import time
from datetime import timedelta

import pgtrigger
from django.db import DatabaseError, transaction
from django.db.models import F, Min
from django.db.models.functions import Now
from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.apps.notifications import conf
from isik.django.apps.notifications.audience.bases import AbstractForceableSubscription
from isik.django.apps.notifications.audience.swapping import get_subscription_model
from isik.django.apps.notifications.channels import Outcome, get_channel
from isik.django.apps.notifications.choices import DeliveryState, Policy, Scope, ShardState, Source
from isik.django.apps.notifications.kinds import _retired, checked_kind, get_kind, matching_variant, write_notice
from isik.django.apps.notifications.partitions import PartitionGone, in_partition
from isik.django.apps.notifications.swapping import (
    get_delivery_attempt_model,
    get_delivery_model,
    get_email_delivery_model,
    get_fan_out_shard_model,
    get_notice_model,
)


logger = logging.getLogger(__name__)


class RetryRefused(Exception):
    """Why a delivery can't be retried by hand - it isn't abandoned, or its target can't be reached."""


def backoff(attempts):
    """How long to wait after the `attempts`-th failed attempt of a run: `BACKOFF`'s entry for it, the last
    once they run out, plus up to a tenth more, so failures that happened together aren't tried together."""
    delays = conf.setting("BACKOFF")
    delay = delays[min(attempts, len(delays)) - 1]
    return timedelta(seconds=delay + random.uniform(0, delay / 10))


def chosen_users(notice, kind):
    """
    The users `notice` is for: its audience's, less those its policy and their subscriptions leave out - each as
    `gets()` decides. A person has one row matching a variant at most, which `VariantGuard` holds them to.
    """
    users = list(kind.audience.recipients(notice))
    if kind.policy == Policy.MANDATORY:
        return users
    subscriptions = matching_variant(
        get_subscription_model()._default_manager.filter(kind_id=notice.kind_id), notice.variant
    )
    rows = {row.user_id: row for row in subscriptions}
    return [user for user in users if gets(rows.get(user.pk), kind.policy)]


def gets(subscription, policy):
    """
    Whether a person gets a notice of a kind with `policy`, `subscription` being their row matching it, or None - the
    one answer the fan-out and every view give: a force, else their own choice, else the policy.
    """
    if policy == Policy.MANDATORY:
        return True
    if subscription is None:
        return policy == Policy.DEFAULT_ON
    # A row not of the person's own choosing exists only while forced (a constraint holds it to that).
    return subscription.forced_enabled if is_forced(subscription) else subscription.enabled


def is_forced(subscription):
    """Whether `subscription` - a row, or None - is forced: never, on a model that can't be."""
    return isinstance(subscription, AbstractForceableSubscription) and subscription.forced


def targets(notice, kind, partition):
    """[(channel name, Target)] - every target `notice` reaches in `partition`, read inside it."""
    with in_partition(partition):
        users = chosen_users(notice, kind)
        return [
            (name, target)
            for name in kind.enabled_channels
            for target in get_channel(name).targets(notice, kind, users)
        ]


def write_deliveries(notice, partition, reached):
    """Writes a delivery, and its child row, for each of `reached` the notice hasn't one for yet. Returns them."""
    written = set(get_delivery_model()._default_manager.filter(notice=notice).values_list("channel", "target_key"))
    deliveries, children = [], {}
    for channel, target in reached:
        if (channel, target.key) in written:
            continue
        written.add((channel, target.key))
        delivery = get_delivery_model()(notice=notice, channel=channel, partition=partition, target_key=target.key)
        deliveries.append(delivery)
        children.setdefault(target.child, []).append(target.child(delivery=delivery, **target.fields))
    get_delivery_model()._default_manager.bulk_create(deliveries)
    for child, rows in children.items():
        child.objects.bulk_create(rows)
    return deliveries


def _single_partition(notice, kind):
    partitions = list(dict.fromkeys(kind.audience.partitions(notice)))
    if len(partitions) != 1:
        raise ValueError(
            _("%(kind)s is single-partition, and its audience named %(count)s partitions for notice %(notice)s.")
            % {"kind": kind.name, "count": len(partitions), "notice": notice.pk}
        )
    return partitions[0]


def _scatter(notice, kind):
    """One shard per partition the cross-partition `notice` reaches."""
    return [
        get_fan_out_shard_model()._default_manager.create(notice=notice, partition=partition)
        for partition in dict.fromkeys(kind.audience.partitions(notice))
    ]


def _deliver_single(notice, kind):
    """The deliveries of the single-partition `notice` - none, when a partitioned project gave it no partition."""
    partition = _single_partition(notice, kind)
    if partition is None and conf.partitioned():
        logger.warning("Notice %s names no partition, so it reaches nobody.", notice.pk)
        return []
    return write_deliveries(notice, partition, targets(notice, kind, partition))


def _stamp(notice):
    get_notice_model()._default_manager.filter(pk=notice.pk).update(fanned_out_at=Now())


def _fan_out_one(notice):
    """Fans one notice out - returning the deliveries to send and the shards to run - and stamps it."""
    kind = get_kind(notice.kind.name)
    deliveries, shards = [], []
    # A kind no longer declared has nobody to reach; its notice is stamped all the same.
    if kind is not None and kind.scope == Scope.CROSS_PARTITION:
        shards = _scatter(notice, kind)
    elif kind is not None:
        deliveries = _deliver_single(notice, kind)
    _stamp(notice)
    return deliveries, shards


def _set_aside(notice, error):
    """
    Stamps a notice that failed to fan out, its work moved to a shard - which is tried again on a backoff
    and abandoned after `MAX_ATTEMPTS` - so it stops heading the queue every fan-out takes from. One
    whose partition is gone is abandoned at once.
    """
    gone = isinstance(error, PartitionGone)
    get_fan_out_shard_model()._default_manager.create(
        notice=notice,
        partition=notice.subject_partition,
        state=ShardState.ABANDONED if gone else ShardState.PENDING,
        attempt_count=1,
        next_attempt_at=timezone.now() + backoff(1),
        last_error=str(error) if gone else f"{type(error).__name__}: {error}",
    )
    _stamp(notice)


def fan_out(batch_size=None):
    """
    Fans out up to `batch_size` (`BATCH_SIZE`) waiting notices, oldest first, in one transaction: each one's
    deliveries - or, for a cross-partition kind, its shards - are written, and it's stamped, together. A
    notice another fan-out holds is skipped, never read twice. One that fails is set aside as a shard to
    try again later, and the others go on. Sends what it wrote once it commits. Returns how many it took.
    """
    with transaction.atomic():
        notices = list(
            get_notice_model()
            ._default_manager.select_related("kind")
            # The notice's row alone: its kind's is read, never held.
            .select_for_update(skip_locked=True, of=("self",))
            .filter(fanned_out_at__isnull=True)
            .order_by("occurred_at", "pk")[: batch_size or conf.setting("BATCH_SIZE")]
        )
        deliveries, shards = [], []
        for notice in notices:
            try:
                with transaction.atomic():
                    written, scattered = _fan_out_one(notice)
            except Exception as error:
                logger.exception("Notice %s could not be fanned out; it's set aside to try again.", notice.pk)
                with transaction.atomic():
                    _set_aside(notice, error)
                written, scattered = [], []
            deliveries += written
            shards += scattered
        transaction.on_commit(lambda: _dispatch(deliveries, shards))
    return len(notices)


def _dispatch(deliveries, shards):
    from isik.django.apps.notifications import tasks

    for delivery in deliveries:
        tasks.send.delay(str(delivery.pk))
    for shard in shards:
        tasks.fan_out_shard.delay(str(shard.pk))


def _claim(queryset, now):
    # With its notice and the notice's kind, which its work reads - held is only its own row.
    return (
        queryset.select_related("notice__kind")
        .select_for_update(skip_locked=True, of=("self",))
        .filter(next_attempt_at__lte=now)
        .first()
    )


def _shard_work(shard, kind):
    """The deliveries and shards a claimed shard writes."""
    notice = shard.notice
    if kind is None:
        return [], []
    if kind.scope == Scope.SINGLE_PARTITION:
        return _deliver_single(notice, kind), []
    if shard.partition is None:
        # A cross-partition notice set aside before it scattered.
        return [], _scatter(notice, kind)
    return write_deliveries(notice, shard.partition, targets(notice, kind, shard.partition)), []


def run_shard(shard_id, claimed_by=""):
    """
    Claims the shard, if it's due and no other worker has it, and writes its notice's deliveries in its
    partition. A failure leaves it to be claimed again after a backoff - or abandoned after `MAX_ATTEMPTS`,
    or at once when its partition is gone. Returns the shard, or None when there was nothing to claim.
    """
    now = timezone.now()
    with transaction.atomic():
        shard = _claim(get_fan_out_shard_model()._default_manager.filter(pk=shard_id, state=ShardState.PENDING), now)
        if shard is None:
            return None
        shard.state, shard.claimed_at, shard.claimed_by = ShardState.CLAIMED, now, claimed_by
        shard.attempt_count += 1
        shard.save()
    mine = get_fan_out_shard_model()._default_manager.filter(pk=shard.pk, state=ShardState.CLAIMED, claimed_at=now)
    try:
        with transaction.atomic():
            deliveries, shards = _shard_work(shard, get_kind(shard.notice.kind.name))
            finished = {"state": ShardState.FINISHED, "finished_at": timezone.now(), "last_error": ""}
            if not mine.update(**finished):
                raise _Superseded
            transaction.on_commit(lambda: _dispatch(deliveries, shards))
    except _Superseded:
        logger.warning("Shard %s was taken back while it ran; its work is dropped.", shard.pk)
        return None
    except Exception as error:
        logger.exception("Shard %s of notice %s failed.", shard.pk, shard.notice_id)
        if isinstance(error, PartitionGone):
            finished = {"state": ShardState.ABANDONED, "last_error": str(error)}
        elif shard.attempt_count >= conf.setting("MAX_ATTEMPTS"):
            finished = {"state": ShardState.ABANDONED, "last_error": f"{type(error).__name__}: {error}"}
        else:
            finished = {
                "state": ShardState.PENDING,
                "next_attempt_at": timezone.now() + backoff(shard.attempt_count),
                "last_error": f"{type(error).__name__}: {error}",
            }
        if not mine.update(**finished):
            return None
    shard.refresh_from_db()
    return shard


class _Superseded(Exception):
    """A worker's claim was taken back and given to another before it finished."""


def _without_nul(value):
    """`value` with every NUL character taken out - Postgres' jsonb and text refuse one."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_without_nul(key): _without_nul(each) for key, each in value.items()}
    if isinstance(value, list):
        return [_without_nul(each) for each in value]
    return value


def _redacted(value):
    redactor = conf.setting("REDACTOR")
    return redactor(value) if redactor is not None and value is not None else value


def _record(delivery, attempt_no, outcome, started_at, duration_ms, triggered_by):
    """Writes the attempt, per `RECORD_ATTEMPTS` - in a savepoint, so a record that can't be written loses the
    record alone and never the delivery's own state."""
    recording = conf.setting("RECORD_ATTEMPTS")
    if recording == "none" or (recording == "failures" and outcome.succeeded):
        return
    try:
        with transaction.atomic():
            get_delivery_attempt_model()._default_manager.create(
                delivery=delivery,
                attempt_no=attempt_no,
                request=_without_nul(_redacted(outcome.request)) or {},
                response=_without_nul(_redacted(outcome.response)),
                status_code=outcome.status_code,
                error=_without_nul(outcome.error),
                succeeded=outcome.succeeded,
                started_at=started_at,
                duration_ms=duration_ms,
                triggered_by=triggered_by,
            )
    except DatabaseError:
        logger.exception("Attempt %s of delivery %s could not be recorded.", attempt_no, delivery.pk)


def _attempt(channel, delivery):
    try:
        return channel.send(delivery)
    except PartitionGone as gone:
        return Outcome(succeeded=False, permanent=True, error=str(gone))
    except Exception as error:
        logger.exception("Delivery %s failed on %s.", delivery.pk, delivery.channel)
        return Outcome(succeeded=False, error=f"{type(error).__name__}: {error}")


def _after(delivery, outcome):
    """Where the delivery goes after an attempt that came to `outcome`."""
    run = delivery.attempt_count - delivery.attempts_before_run
    if outcome.succeeded:
        return {"state": DeliveryState.SENT, "sent_at": timezone.now()}
    if outcome.permanent or run >= conf.setting("MAX_ATTEMPTS"):
        return {"state": DeliveryState.ABANDONED}
    return {"state": DeliveryState.PENDING, "next_attempt_at": timezone.now() + backoff(run)}


def send(delivery_id, triggered_by="send"):
    """
    Claims the delivery, if it's due and no other worker has it, and sends it on its channel. Sent, it's
    done; failed, it's due again after a backoff - unless the failure is permanent or its run has had
    `MAX_ATTEMPTS`, when it's abandoned. Returns the delivery, or None when there was nothing to claim, or
    when it was taken back while this worker sent it.
    """
    now = timezone.now()
    with transaction.atomic():
        delivery = _claim(
            get_delivery_model()._default_manager.filter(pk=delivery_id, state=DeliveryState.PENDING), now
        )
        if delivery is None:
            return None
        # A retry asked for by hand says so, whichever task got to it first.
        triggered_by = delivery.retry_trigger or triggered_by
        delivery.state, delivery.claimed_at, delivery.retry_trigger = DeliveryState.SENDING, now, ""
        delivery.attempt_count += 1
        delivery.save()
    channel = get_channel(delivery.channel)
    started_at, started = timezone.now(), time.monotonic()
    outcome = _attempt(channel, delivery)
    duration_ms = round((time.monotonic() - started) * 1000)
    after = _after(delivery, outcome)
    mine = get_delivery_model()._default_manager.filter(pk=delivery.pk, state=DeliveryState.SENDING, claimed_at=now)
    with transaction.atomic():
        if not mine.update(**after):
            logger.warning("Delivery %s was taken back while it was sent; this attempt is dropped.", delivery.pk)
            return None
        _record(delivery, delivery.attempt_count, outcome, started_at, duration_ms, triggered_by)
        try:
            channel.attempted(delivery, outcome)
        except PartitionGone:
            pass
        if after["state"] == DeliveryState.PENDING:
            transaction.on_commit(lambda: _send_at(delivery.pk, after["next_attempt_at"]))
    delivery.refresh_from_db()
    return delivery


def _send_at(delivery_id, due):
    from isik.django.apps.notifications import tasks

    tasks.send.apply_async(args=[str(delivery_id)], eta=due)


def retry_delivery(delivery):
    """
    Sends an abandoned delivery (or its pk) again - due now, a new run with `MAX_ATTEMPTS` of its own, its
    next attempt recorded as manual. Refused, with `RetryRefused` saying why, for a delivery that isn't
    abandoned, or whose channel can't reach its target any more - a parked webhook endpoint, say, which is
    unparked first, as its own decision. Returns the delivery.
    """
    pk = getattr(delivery, "pk", delivery)
    with transaction.atomic():
        row = get_delivery_model()._default_manager.select_for_update().get(pk=pk)
        # Held, so prune_notices - which skips a locked notice - can't delete it while it's retried.
        get_notice_model()._default_manager.select_for_update().filter(pk=row.notice_id).exists()
        if row.notice.kind.source == Source.RECORDED:
            raise RetryRefused(_("This email was sent by its caller and only recorded here - isik can't send it."))
        if row.state != DeliveryState.ABANDONED:
            raise RetryRefused(
                _("Only an abandoned delivery is retried, and this one is %(state)s.") % {"state": row.state}
            )
        reason = get_channel(row.channel).unretriable(row)
        if reason is not None:
            raise RetryRefused(reason)
        get_delivery_model()._default_manager.filter(pk=pk).update(
            state=DeliveryState.PENDING,
            next_attempt_at=Now(),
            attempts_before_run=F("attempt_count"),
            retry_trigger="manual",
        )
        transaction.on_commit(lambda: _dispatch([row], []))
    row.refresh_from_db()
    return row


def record_email(kind, *, address, send, user=None, variant=None, about=None, snapshot=None, partition=None, key=None):
    """
    Calls `send()` - which sends an email to `address` now, its own way - and then records that it did: a
    notice of the recorded `kind` (`declare_kind(..., source=Source.RECORDED)`) about the row `about`, if
    any, already fanned out, and its email delivery in `partition`, sent or abandoned by whether `send()`
    raised, with the attempt per `RECORD_ATTEMPTS`. Nothing of the message is kept, only where it went.

    `key`, when given, names one email across the tries of a sender that retries it - a Celery task's id,
    say: a later call with the same key adds its attempt to the delivery the first wrote, which ends sent
    or abandoned by the last, rather than recording another email.

    `send()` is called first, before anything here can fail, and what it raises is all that's raised -
    again, once it's recorded. Nothing after it can stop the email: a kind that isn't a declared recorded
    one, a retired kind, a record that can't be written are each logged, and the email goes unrecorded.
    The record is written after `send()`, never before, so nothing waits in the queue for a worker to send
    again. Returns the delivery, or None when nothing was recorded.
    """
    record = functools.partial(
        _recorded,
        kind,
        started_at=timezone.now(),
        started=time.monotonic(),
        address=address,
        user=user,
        variant=variant,
        about=about,
        snapshot=snapshot,
        partition=partition,
        key=key,
    )
    try:
        send()
    except Exception as error:
        record(Outcome(succeeded=False, request={"to": address}, error=f"{type(error).__name__}: {error}"))
        raise
    return record(Outcome(succeeded=True, request={"to": address}))


def _recorded(kind, outcome, *, started_at, started, address, user, variant, about, snapshot, partition, key):
    try:
        duration_ms = round((time.monotonic() - started) * 1000)
        name = checked_kind(kind, Source.RECORDED, variant)
        with transaction.atomic():
            earlier = _recorded_under(key)
            if earlier is not None:
                return _tried_again(earlier, outcome, started_at, duration_ms)
            if _retired(name):
                logger.warning("An email of %s, which is retired, was sent and not recorded.", name)
                return None
            notice = write_notice(
                name, variant=variant, subject=about, snapshot=snapshot, partition=partition, fanned_out_at=Now()
            )
            delivery = get_delivery_model()._default_manager.create(
                notice=notice,
                channel="email",
                partition=partition,
                target_key=f"email:{address.lower()}",
                attempt_count=1,
                **_finished(outcome),
            )
            get_email_delivery_model()._default_manager.create(
                delivery=delivery,
                address=address,
                user_id=str(user.pk) if user is not None else None,
                record_key=key,
            )
            _record(delivery, 1, outcome, started_at, duration_ms, "record_email")
    except Exception:
        logger.exception("An email of %s to %s was sent and could not be recorded.", kind, address)
        return None
    return delivery


def _finished(outcome):
    """A recorded delivery's state after an attempt that came to `outcome` - no other try is isik's."""
    if outcome.succeeded:
        return {"state": DeliveryState.SENT, "sent_at": timezone.now()}
    return {"state": DeliveryState.ABANDONED, "sent_at": None}


def _recorded_under(key):
    """The delivery recorded under `key` before, held for this attempt - or None."""
    if key is None:
        return None
    earlier = get_email_delivery_model()._default_manager.select_for_update().filter(record_key=key).first()
    return earlier.delivery if earlier is not None else None


def _tried_again(delivery, outcome, started_at, duration_ms):
    attempt_no = delivery.attempt_count + 1
    get_delivery_model()._default_manager.filter(pk=delivery.pk).update(attempt_count=attempt_no, **_finished(outcome))
    _record(delivery, attempt_no, outcome, started_at, duration_ms, "record_email")
    delivery.refresh_from_db()
    return delivery


def reap_stranded():
    """
    Takes back what a dead worker left: a delivery sending, or a shard claimed, for longer than
    `STRANDED_AFTER` is abandoned when its run has had `MAX_ATTEMPTS`, and waits again otherwise. Then up
    to `HOUSEKEEPING_BATCH` deliveries and shards overdue by `STRANDED_AFTER` - their task lost - are asked
    for again, and a fan-out is started if any notice is waiting. Returns {"deliveries": n, "shards": n}.
    """
    from isik.django.apps.notifications import tasks

    now = timezone.now()
    stranded = now - conf.setting("STRANDED_AFTER")
    attempts, limit = conf.setting("MAX_ATTEMPTS"), conf.setting("HOUSEKEEPING_BATCH")
    sending = get_delivery_model()._default_manager.filter(state=DeliveryState.SENDING, claimed_at__lt=stranded)
    sending.filter(attempt_count__gte=F("attempts_before_run") + attempts).update(state=DeliveryState.ABANDONED)
    sending.update(state=DeliveryState.PENDING)
    claimed = get_fan_out_shard_model()._default_manager.filter(state=ShardState.CLAIMED, claimed_at__lt=stranded)
    claimed.filter(attempt_count__gte=attempts).update(state=ShardState.ABANDONED)
    claimed.update(state=ShardState.PENDING)
    deliveries = list(
        get_delivery_model()
        ._default_manager.filter(state=DeliveryState.PENDING, next_attempt_at__lte=stranded)
        .order_by("next_attempt_at")[:limit]
    )
    shards = list(
        get_fan_out_shard_model()
        ._default_manager.filter(state=ShardState.PENDING, next_attempt_at__lte=stranded)
        .order_by("next_attempt_at")[:limit]
    )
    for delivery in deliveries:
        tasks.send.delay(str(delivery.pk), triggered_by="reap_stranded")
    for shard in shards:
        tasks.fan_out_shard.delay(str(shard.pk))
    if get_notice_model()._default_manager.filter(fanned_out_at__isnull=True).exists():
        tasks.fan_out.delay()
    return {"deliveries": len(deliveries), "shards": len(shards)}


def _protections(*models):
    return [f"{model._meta.label}:{trigger.name}" for model in models for trigger in model._meta.triggers]


def prune_attempts():
    """Deletes up to `HOUSEKEEPING_BATCH` attempts older than `ATTEMPT_RETENTION` - none when it's None.
    Returns how many - a full batch says there may be more."""
    retention = conf.setting("ATTEMPT_RETENTION")
    if retention is None:
        return 0
    old = get_delivery_attempt_model()._default_manager.filter(started_at__lt=timezone.now() - retention)
    pruned = [attempt.pk for attempt in old[: conf.setting("HOUSEKEEPING_BATCH")]]
    with pgtrigger.ignore(*_protections(get_delivery_attempt_model())):
        get_delivery_attempt_model()._default_manager.filter(pk__in=pruned).delete()
    return len(pruned)


def prune_notices():
    """
    Deletes up to `HOUSEKEEPING_BATCH` notices older than `NOTICE_RETENTION` - none when it's None - that are
    done with: fanned out, with no delivery still to send and no shard still to run. A notice still waiting
    is never pruned, however old: that's the backlog `oldest_unfanned_notice_age()` reports. Nor is one a
    manual retry holds. Returns how many - a full batch says there may be more.
    """
    retention = conf.setting("NOTICE_RETENTION")
    if retention is None:
        return 0
    with transaction.atomic():
        done = (
            get_notice_model()
            ._default_manager.select_for_update(skip_locked=True)
            .filter(fanned_out_at__isnull=False, occurred_at__lt=timezone.now() - retention)
            .exclude(deliveries__state__in=[DeliveryState.PENDING, DeliveryState.SENDING])
            .exclude(shards__state__in=[ShardState.PENDING, ShardState.CLAIMED])
        )
        pruned = [notice.pk for notice in done[: conf.setting("HOUSEKEEPING_BATCH")]]
        with pgtrigger.ignore(*_protections(get_notice_model(), get_delivery_attempt_model())):
            get_notice_model()._default_manager.filter(pk__in=pruned).delete()
    return len(pruned)


def oldest_unfanned_notice_age():
    """How long the oldest notice has waited to be fanned out, or None when none is - for a health check:
    a fan-out that stopped running raises no error, and this is how it shows."""
    oldest = (
        get_notice_model()
        ._default_manager.filter(fanned_out_at__isnull=True)
        .aggregate(oldest=Min("occurred_at"))["oldest"]
    )
    return timezone.now() - oldest if oldest is not None else None
