# ruff: noqa: F811 - the webhook fixtures are test_pipeline_details', imported by name and requested by it.
"""What the pipeline does when things go wrong: a notice that can't fan out, a partition that's gone, a
worker taken over, a response it can't store, the reaper and pruning at their limits, and manual retries."""

import logging
import threading
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from isik.django.apps.notifications import pipeline, tasks
from isik.django.apps.notifications.audience.swapping import get_endpoint_model
from isik.django.apps.notifications.audiences import Audience
from isik.django.apps.notifications.channels import webhook as webhook_module
from isik.django.apps.notifications.models import (
    Delivery,
    DeliveryAttempt,
    DeliveryState,
    FanOutShard,
    Notice,
    Scope,
    ShardState,
)
from isik.django.apps.notifications.partitions import PartitionGone
from isik.django.apps.notifications.pipeline import RetryRefused, retry_delivery
from tests.django.apps.notifications.test_pipeline import deliveries, delivery_on, fan_out, flag, redeclared, ship
from tests.django.apps.notifications.test_pipeline_details import hooked, receiver, shipped  # noqa: F401  # noqa: F401
from tests.testapp.models import EmailUser, Parcel


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_jitter(monkeypatch):
    monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: 0)


@pytest.fixture
def partitions(settings):
    """Partitions on - "north" and "south" exist, and entering one is recorded: [key, ...]."""
    entered = []

    @contextmanager
    def enter(key):
        entered.append(key)
        yield

    settings.ISIK_NOTIFICATIONS = {
        **settings.ISIK_NOTIFICATIONS,
        "PARTITIONS": lambda: ["north", "south"],
        "IN_PARTITION": enter,
    }
    return entered


class InPartition(Audience):
    def __init__(self, partition, users=()):
        self.partition, self.users = partition, users

    def partitions(self, notice):
        return [self.partition]

    def recipients(self, notice):
        return self.users


class TestANoticeWhosePartitionIsGone:
    def test_its_set_aside_shard_is_abandoned_at_once_and_nothing_is_read(
        self, monkeypatch, partitions, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", audience=InPartition("gone", [bob])):
            fan_out(django_capture_on_commit_callbacks)

        [shard] = FanOutShard.objects.filter(notice=notice)
        assert (shard.state, shard.last_error) == (ShardState.ABANDONED, "The partition 'gone' no longer exists.")
        assert deliveries(notice) == []
        # Never entered, so never read from wherever a gone partition would fall through to.
        assert partitions == []

    def test_a_shard_into_one_is_abandoned_at_once(self, monkeypatch, partitions, parcel, bob):
        class Everywhere(InPartition):
            spans_partitions = True

        shard = FanOutShard.objects.create(notice=ship(parcel), partition="gone")

        with redeclared(monkeypatch, "parcel_shipped", audience=Everywhere(None, [bob]), scope=Scope.CROSS_PARTITION):
            ran = pipeline.run_shard(shard.pk)

        assert (ran.state, ran.attempt_count, ran.last_error) == (
            ShardState.ABANDONED,
            1,
            "The partition 'gone' no longer exists.",
        )

    def test_a_delivery_into_one_is_abandoned_at_once(self, shipped, partitions):
        inbox = delivery_on(shipped, "inbox")
        Delivery.objects.filter(pk=inbox.pk).update(partition="gone")

        sent = pipeline.send(inbox.pk)

        assert sent.state == DeliveryState.ABANDONED
        assert DeliveryAttempt.objects.get().error == "The partition 'gone' no longer exists."

    def test_a_partitioned_notice_naming_no_partition_reaches_nobody(
        self, monkeypatch, caplog, partitions, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        notice = ship(parcel)

        with (
            redeclared(monkeypatch, "parcel_shipped", audience=InPartition(None, [bob])),
            caplog.at_level(logging.WARNING),
        ):
            fan_out(django_capture_on_commit_callbacks)

        notice.refresh_from_db()
        assert notice.fanned_out_at is not None
        assert deliveries(notice) == []
        assert not FanOutShard.objects.exists()
        assert f"Notice {notice.pk} names no partition, so it reaches nobody." in [
            r.getMessage() for r in caplog.records
        ]


class TestSetAsideNotices:
    def test_a_cross_partition_one_scatters_from_its_shard(
        self, monkeypatch, partitions, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        class ScattersOnSecondTry(Audience):
            spans_partitions = True
            tries = 0

            def partitions(self, notice):
                ScattersOnSecondTry.tries += 1
                if ScattersOnSecondTry.tries == 1:
                    raise RuntimeError("down")
                return ["north", "south"]

            def recipients(self, notice):
                return [bob]

        notice = ship(parcel)
        with redeclared(monkeypatch, "parcel_shipped", audience=ScattersOnSecondTry(), scope=Scope.CROSS_PARTITION):
            fan_out(django_capture_on_commit_callbacks)
            [aside] = FanOutShard.objects.filter(notice=notice)
            assert aside.partition is None
            FanOutShard.objects.filter(pk=aside.pk).update(next_attempt_at=timezone.now())
            dispatched.clear()

            with django_capture_on_commit_callbacks(execute=True):
                assert pipeline.run_shard(aside.pk).state == ShardState.FINISHED

        scattered = FanOutShard.objects.filter(notice=notice).exclude(pk=aside.pk).order_by("partition")
        assert [shard.partition for shard in scattered] == ["north", "south"]
        assert dispatched == [("fan_out_shard", (str(shard.pk),), {}) for shard in scattered]


class TestAWorkerTakenOver:
    def test_a_shard_finished_after_its_claim_was_given_away_writes_nothing(self, monkeypatch, caplog, parcel):
        shard = FanOutShard.objects.create(notice=ship(parcel), partition=None)
        real = pipeline._shard_work

        def taken_over(shard, kind):
            FanOutShard.objects.filter(pk=shard.pk).update(claimed_at=timezone.now() + timedelta(seconds=1))
            return real(shard, kind)

        monkeypatch.setattr(pipeline, "_shard_work", taken_over)

        with caplog.at_level(logging.WARNING):
            assert pipeline.run_shard(shard.pk) is None

        assert FanOutShard.objects.get(pk=shard.pk).state == ShardState.CLAIMED
        assert not Delivery.objects.exists()
        assert f"Shard {shard.pk} was taken back while it ran; its work is dropped." in [
            record.getMessage() for record in caplog.records
        ]

    def test_a_failing_shard_whose_claim_was_given_away_writes_nothing(self, monkeypatch, parcel):
        shard = FanOutShard.objects.create(notice=ship(parcel), partition=None)

        def down(shard, kind):
            raise RuntimeError("down")

        def taken_over(attempts):
            # Another worker claims it while this one is still deciding what its failure means.
            FanOutShard.objects.filter(pk=shard.pk).update(claimed_at=timezone.now() + timedelta(seconds=1))
            return timedelta(seconds=60)

        monkeypatch.setattr(pipeline, "_shard_work", down)
        monkeypatch.setattr(pipeline, "backoff", taken_over)

        assert pipeline.run_shard(shard.pk) is None
        assert FanOutShard.objects.get(pk=shard.pk).last_error == ""

    def test_a_delivery_sent_after_its_claim_was_given_away_writes_nothing(self, monkeypatch, caplog, shipped):
        inbox = delivery_on(shipped, "inbox")

        def taken_over(self, delivery):
            Delivery.objects.filter(pk=delivery.pk).update(attempt_count=7, claimed_at=timezone.now() + timedelta(1))
            return pipeline.Outcome(succeeded=False)

        monkeypatch.setattr("isik.django.apps.notifications.channels.inbox.InboxChannel.send", taken_over)

        with caplog.at_level(logging.WARNING):
            assert pipeline.send(inbox.pk) is None

        row = Delivery.objects.get(pk=inbox.pk)
        assert (row.state, row.attempt_count) == (DeliveryState.SENDING, 7)
        assert not DeliveryAttempt.objects.exists()
        assert f"Delivery {inbox.pk} was taken back while it was sent; this attempt is dropped." in [
            record.getMessage() for record in caplog.records
        ]


class TestWhatCantBeStored:
    def test_a_nul_in_a_response_is_dropped_and_the_delivery_moves_on(self, hooked, receiver):
        receiver.status, receiver.text = 500, "bad\x00body"
        receiver.headers = {"X-Odd": "a\x00b"}

        failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.PENDING
        attempt = DeliveryAttempt.objects.get()
        assert attempt.response["body"] == "badbody"
        assert attempt.response["headers"]["x-odd"] == "ab"

    def test_an_attempt_that_cant_be_written_loses_the_record_alone(self, monkeypatch, caplog, hooked, receiver):
        receiver.status = 500

        def refuse(**kwargs):
            raise DatabaseError("can't")

        monkeypatch.setattr(DeliveryAttempt.objects, "create", refuse)

        with caplog.at_level(logging.ERROR):
            failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.PENDING
        assert f"Attempt 1 of delivery {failed.pk} could not be recorded." in [r.getMessage() for r in caplog.records]

    def test_nul_is_taken_out_of_nested_values(self):
        assert pipeline._without_nul({"a\x00": ["b\x00", {"c": "d\x00"}, 1, None]}) == {"a": ["b", {"c": "d"}, 1, None]}


class TestTheReaperAtItsLimits:
    def test_a_stranded_delivery_whose_run_is_spent_is_abandoned(self, shipped, settings, dispatched):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "MAX_ATTEMPTS": 2}
        long_ago = timezone.now() - timedelta(hours=1)
        inbox, email = delivery_on(shipped, "inbox"), delivery_on(shipped, "email")
        Delivery.objects.filter(pk=inbox.pk).update(
            state=DeliveryState.SENDING, claimed_at=long_ago, attempt_count=5, attempts_before_run=3
        )
        Delivery.objects.filter(pk=email.pk).update(
            state=DeliveryState.SENDING, claimed_at=long_ago, attempt_count=5, attempts_before_run=4
        )

        pipeline.reap_stranded()

        assert Delivery.objects.get(pk=inbox.pk).state == DeliveryState.ABANDONED
        assert Delivery.objects.get(pk=email.pk).state == DeliveryState.PENDING

    def test_a_stranded_shard_whose_attempts_are_spent_is_abandoned(self, parcel, settings, dispatched):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "MAX_ATTEMPTS": 2}
        long_ago = timezone.now() - timedelta(hours=1)
        notice = ship(parcel)
        spent = FanOutShard.objects.create(
            notice=notice, partition="a", state=ShardState.CLAIMED, claimed_at=long_ago, attempt_count=2
        )
        fresh = FanOutShard.objects.create(
            notice=notice, partition="b", state=ShardState.CLAIMED, claimed_at=long_ago, attempt_count=1
        )

        pipeline.reap_stranded()

        assert FanOutShard.objects.get(pk=spent.pk).state == ShardState.ABANDONED
        assert FanOutShard.objects.get(pk=fresh.pk).state == ShardState.PENDING

    def test_only_whats_overdue_by_the_grace_is_asked_for_again(self, shipped, dispatched):
        inbox, email = delivery_on(shipped, "inbox"), delivery_on(shipped, "email")
        Delivery.objects.filter(pk=inbox.pk).update(next_attempt_at=timezone.now() - timedelta(minutes=11))
        Delivery.objects.filter(pk=email.pk).update(next_attempt_at=timezone.now() - timedelta(minutes=9))

        assert pipeline.reap_stranded() == {"deliveries": 1, "shards": 0}
        assert dispatched == [("send", (str(inbox.pk),), {"triggered_by": "reap_stranded"})]

    def test_no_more_than_a_batch_most_overdue_first(self, shipped, settings, dispatched):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "HOUSEKEEPING_BATCH": 1}
        inbox, email = delivery_on(shipped, "inbox"), delivery_on(shipped, "email")
        Delivery.objects.filter(pk=inbox.pk).update(next_attempt_at=timezone.now() - timedelta(hours=1))
        Delivery.objects.filter(pk=email.pk).update(next_attempt_at=timezone.now() - timedelta(hours=2))
        FanOutShard.objects.create(notice=shipped, partition="a", next_attempt_at=timezone.now() - timedelta(hours=1))
        older = FanOutShard.objects.create(
            notice=shipped, partition="b", next_attempt_at=timezone.now() - timedelta(hours=2)
        )

        assert pipeline.reap_stranded() == {"deliveries": 1, "shards": 1}
        assert dispatched == [
            ("send", (str(email.pk),), {"triggered_by": "reap_stranded"}),
            ("fan_out_shard", (str(older.pk),), {}),
        ]


class TestPruningInBatches:
    def test_attempts(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "RECORD_ATTEMPTS": "all",
            "ATTEMPT_RETENTION": timedelta(0),
            "HOUSEKEEPING_BATCH": 1,
        }
        receiver.status = 500
        pipeline.send(hooked[0].pk)
        Delivery.objects.filter(pk=hooked[0].pk).update(next_attempt_at=timezone.now())
        pipeline.send(hooked[0].pk)

        assert pipeline.prune_attempts() == 1
        assert DeliveryAttempt.objects.count() == 1

    def test_notices(self, parcel, alice, settings):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "NOTICE_RETENTION": timedelta(0),
            "HOUSEKEEPING_BATCH": 1,
        }
        for notice in (ship(parcel), flag(alice)):
            Notice.objects.filter(pk=notice.pk).update(fanned_out_at=timezone.now())

        assert pipeline.prune_notices() == 1
        assert Notice.objects.count() == 1

    def test_a_full_batch_starts_another(self, monkeypatch, settings, dispatched):
        settings.ISIK_NOTIFICATIONS = {"HOUSEKEEPING_BATCH": 2}
        asked = []
        for name in ("prune_attempts", "prune_notices"):
            counts = iter([2, 1])
            monkeypatch.setattr(pipeline, name, lambda _counts=counts: next(_counts))
            monkeypatch.setattr(getattr(tasks, name), "delay", lambda _name=name: asked.append(_name))

        assert (tasks.prune_attempts(), tasks.prune_attempts()) == (2, 1)
        assert (tasks.prune_notices(), tasks.prune_notices()) == (2, 1)
        assert asked == ["prune_attempts", "prune_notices"]


@pytest.mark.django_db(transaction=True)
def test_pruning_skips_a_notice_a_retry_holds(settings):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_RETENTION": timedelta(0)}
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")
    parcel = Parcel.objects.create(sender=alice)
    notice = ship(parcel)
    Notice.objects.filter(pk=notice.pk).update(fanned_out_at=timezone.now())
    held, release = threading.Event(), threading.Event()

    def hold():
        with transaction.atomic():
            Notice.objects.select_for_update().get(pk=notice.pk)
            held.set()
            release.wait(5)
        connection.close()

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    try:
        assert pipeline.prune_notices() == 0
    finally:
        release.set()
        holder.join(5)
    assert pipeline.prune_notices() == 1


@pytest.fixture
def abandoned(hooked, receiver):
    receiver.status = 404
    delivery = pipeline.send(hooked[0].pk)
    assert delivery.state == DeliveryState.ABANDONED
    receiver.status = 200
    return delivery


class TestRetryingByHand:
    def test_an_abandoned_delivery_runs_again_now_recorded_as_manual(
        self,
        abandoned,
        receiver,
        settings,
        dispatched,
        django_capture_on_commit_callbacks,
    ):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}

        with django_capture_on_commit_callbacks(execute=True):
            retried = retry_delivery(abandoned)

        assert (retried.state, retried.attempts_before_run, retried.retry_trigger) == (
            DeliveryState.PENDING,
            1,
            "manual",
        )
        assert retried.next_attempt_at <= timezone.now()
        assert dispatched == [("send", (str(abandoned.pk),), {})]
        sent = pipeline.send(abandoned.pk, triggered_by="reap_stranded")
        assert (sent.state, sent.attempt_count, sent.retry_trigger) == (DeliveryState.SENT, 2, "")
        assert DeliveryAttempt.objects.get(attempt_no=2).triggered_by == "manual"

    def test_by_its_key(self, abandoned):
        assert retry_delivery(abandoned.pk).state == DeliveryState.PENDING

    def test_each_run_has_its_own_attempts_and_backoff(self, abandoned, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "MAX_ATTEMPTS": 2, "BACKOFF": (60, 7200)}
        receiver.status = 500
        retry_delivery(abandoned)

        first = pipeline.send(abandoned.pk)
        assert first.state == DeliveryState.PENDING
        assert first.next_attempt_at - timezone.now() < timedelta(seconds=61)
        Delivery.objects.filter(pk=abandoned.pk).update(next_attempt_at=timezone.now())
        assert pipeline.send(abandoned.pk).state == DeliveryState.ABANDONED

    @pytest.mark.parametrize("state", [DeliveryState.PENDING, DeliveryState.SENDING, DeliveryState.SENT])
    def test_only_an_abandoned_one(self, abandoned, state):
        Delivery.objects.filter(pk=abandoned.pk).update(state=state)

        with pytest.raises(RetryRefused, match=f"^Only an abandoned delivery is retried, and this one is {state}.$"):
            retry_delivery(abandoned)

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"parked_at": timezone.now()}, "The endpoint is parked - unpark it first."),
            ({"is_active": False}, "The endpoint is inactive."),
        ],
    )
    def test_not_while_its_endpoint_cant_be_reached(self, abandoned, change, reason):
        endpoint_id = Delivery.objects.get(pk=abandoned.pk).webhook.endpoint_id
        get_endpoint_model().objects.filter(pk=endpoint_id).update(**change)

        with pytest.raises(RetryRefused, match=f"^{reason}$"):
            retry_delivery(abandoned)
        assert Delivery.objects.get(pk=abandoned.pk).state == DeliveryState.ABANDONED

    def test_not_once_its_endpoint_is_gone(self, abandoned):
        get_endpoint_model().objects.filter(pk=abandoned.webhook.endpoint_id).delete()

        with pytest.raises(RetryRefused, match="^The endpoint no longer exists.$"):
            retry_delivery(abandoned)

    def test_not_once_its_partition_is_gone(self, abandoned, partitions):
        Delivery.objects.filter(pk=abandoned.pk).update(partition="gone")

        with pytest.raises(RetryRefused, match="^The partition 'gone' no longer exists.$"):
            retry_delivery(abandoned)

    def test_a_channel_may_be_retried_by_default_while_its_partition_exists(self, shipped, partitions):
        from isik.django.apps.notifications.channels.inbox import InboxChannel

        inbox = delivery_on(shipped, "inbox")
        Delivery.objects.filter(pk=inbox.pk).update(partition="north")
        inbox.refresh_from_db()

        assert InboxChannel().unretriable(inbox) is None


class TestTheWebhooksLimits:
    def test_a_body_without_end_is_read_no_further_than_the_limit(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "RECORD_ATTEMPTS": "all",
            "MAX_RESPONSE_BYTES": 10,
        }
        receiver.text = "x" * 100000

        pipeline.send(hooked[0].pk)

        assert DeliveryAttempt.objects.get().response["body"] == "x" * 10

    def test_an_answer_slower_than_the_timeout_in_all_is_given_up(self, monkeypatch, hooked, receiver):
        # The send's own start, the webhook's deadline, then the clock after the first chunk.
        clock = iter([0.0, 0.0, 100.0])
        monkeypatch.setattr(webhook_module.time, "monotonic", lambda: next(clock, 100.0))
        receiver.chunks = [b"a", b"b"]

        failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.PENDING
        assert DeliveryAttempt.objects.get().error == "TooSlow: The endpoint took longer than 10 seconds to answer."

    def test_a_redirect_isnt_followed(self, hooked, receiver):
        receiver.status, receiver.headers = 302, {"Location": "http://127.0.0.1/admin"}

        failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.ABANDONED
        assert len(receiver.requests) == 1

    def test_which_statuses_are_retried_is_a_setting(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RETRYABLE_STATUSES": (404,)}
        receiver.status = 404

        assert pipeline.send(hooked[0].pk).state == DeliveryState.PENDING

    def test_a_refused_address_is_permanent_and_names_no_address(self, hooked):
        get_endpoint_model().objects.filter(pk=hooked[1].pk).update(url="http://127.0.0.1:9/in")

        failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.ABANDONED
        assert DeliveryAttempt.objects.get().error == "The endpoint is refused: it resolves to a loopback address."

    def test_through_a_proxy_the_resolved_address_is_still_checked(self, hooked, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "WEBHOOK_PROXY": "http://proxy.internal:3128"}
        get_endpoint_model().objects.filter(pk=hooked[1].pk).update(url="http://127.0.0.1:9/in")

        failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.ABANDONED
        assert DeliveryAttempt.objects.get().error == "The endpoint is refused: it resolves to a loopback address."

    def test_a_url_that_isnt_http_is_never_sent(self, hooked, receiver):
        get_endpoint_model().objects.filter(pk=hooked[1].pk).update(url="ftp://hooks.example/in")

        failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.ABANDONED
        assert receiver.requests == []
        assert DeliveryAttempt.objects.get().error == "The endpoint's URL isn't http or https."

    def test_an_endpoint_url_is_http_or_https(self):
        from django.core.exceptions import ValidationError

        endpoint = get_endpoint_model()(url="ftp://hooks.example/in", secret="s")

        with pytest.raises(ValidationError) as refused:
            endpoint.full_clean()
        assert "url" in refused.value.message_dict


class TestParkingOnce:
    def test_two_failures_together_park_it_and_say_so_once(self, hooked, receiver, settings):
        from isik.django.apps.notifications.channels.webhook import WebhookChannel
        from isik.django.apps.notifications.signals import endpoint_parked

        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "DISABLE_AFTER": 1}
        delivery = Delivery.objects.get(pk=hooked[0].pk)
        heard = []

        def listen(sender, endpoint, **kwargs):
            heard.append(endpoint.pk)

        endpoint_parked.connect(listen)
        try:
            WebhookChannel().attempted(delivery, pipeline.Outcome(succeeded=False))
            WebhookChannel().attempted(delivery, pipeline.Outcome(succeeded=False))
        finally:
            endpoint_parked.disconnect(listen)

        assert heard == [hooked[1].pk]


def test_the_partition_gone_after_an_attempt_is_no_failure_of_the_attempt(monkeypatch, shipped, partitions):
    inbox = delivery_on(shipped, "inbox")
    Delivery.objects.filter(pk=inbox.pk).update(partition="north")

    def gone(self, delivery, outcome):
        raise PartitionGone("north")

    monkeypatch.setattr("isik.django.apps.notifications.channels.Channel.attempted", gone)

    assert pipeline.send(inbox.pk).state == DeliveryState.SENT
