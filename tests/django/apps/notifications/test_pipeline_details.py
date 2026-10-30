"""The pipeline's details, each pinned exactly: what it logs, orders, records, sends and passes on."""

import json
import logging
import threading
from contextlib import contextmanager, nullcontext
from datetime import timedelta

import httpx
import pgtrigger
import pytest
from django.core import mail
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from isik.django.apps.common.db import flushing_through_truncate_protection
from isik.django.apps.notifications import checks, kinds, pipeline, sync, tasks
from isik.django.apps.notifications.audience.helpers import subscribe, unsubscribe
from isik.django.apps.notifications.audience.swapping import (
    get_endpoint_kind_model,
    get_endpoint_model,
    get_subscription_model,
)
from isik.django.apps.notifications.audiences import Audience, Subscribers
from isik.django.apps.notifications.channels import Channel, Target, register_channel, registered
from isik.django.apps.notifications.channels import webhook as webhook_module
from isik.django.apps.notifications.kinds import raise_notice
from isik.django.apps.notifications.management.commands.notices import describe
from isik.django.apps.notifications.models import (
    Delivery,
    DeliveryAttempt,
    DeliveryState,
    FanOutShard,
    InboxDelivery,
    Notice,
    NoticeKindPolicy,
    Policy,
    Scope,
    ShardState,
)
from isik.django.apps.notifications.signals import endpoint_parked
from tests.django.apps.notifications import kind_named
from tests.django.apps.notifications.test_operations import notices as run_notices
from tests.django.apps.notifications.test_pipeline import (
    Receiver,
    deliveries,
    delivery_on,
    fan_out,
    flag,
    redeclared,
    ship,
)
from tests.testapp.models import EmailUser, Parcel


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_jitter(monkeypatch):
    monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: 0)


@pytest.fixture
def entered(settings):
    """Partitions on, entering one recorded - [key, ...]."""
    keys = []

    @contextmanager
    def in_partition(key):
        keys.append(key)
        yield

    settings.ISIK_NOTIFICATIONS = {
        **settings.ISIK_NOTIFICATIONS,
        "PARTITIONS": lambda: ["north"],
        "IN_PARTITION": in_partition,
    }
    return keys


def in_north(delivery):
    Delivery.objects.filter(pk=delivery.pk).update(partition="north")
    delivery.refresh_from_db()
    return delivery


class TestTheMirrorsDatabase:
    def test_it_writes_to_the_database_it_was_given(self, monkeypatch):
        used = []

        @contextmanager
        def atomic(using):
            used.append(("atomic", using))
            yield

        class Rows:
            def update_or_create(self, **kwargs):
                return NoticeKindPolicy(name=kwargs["name"]), True

        monkeypatch.setattr(sync.transaction, "atomic", atomic)
        monkeypatch.setattr(NoticeKindPolicy.objects, "using", lambda alias: used.append(("using", alias)) or Rows())

        mirror_kinds = sync.mirror_kinds(using="elsewhere")

        assert used[0] == ("atomic", "elsewhere")
        assert set(used[1:]) == {("using", "elsewhere")}
        assert len(mirror_kinds) == len(kinds.kinds())


class TestReadingRetiredKindsFromEachDatabase:
    def test_each_database_is_read_and_a_broken_one_skipped(self, monkeypatch):
        NoticeKindPolicy.objects.filter(name="parcel_shipped").update(retired_at=timezone.now())
        real = NoticeKindPolicy.objects.using

        def using(alias):
            if alias == "broken":
                raise DatabaseError("no such table")
            return {"default": real("default"), "empty": NoticeKindPolicy.objects.none()}[alias]

        monkeypatch.setattr(NoticeKindPolicy.objects, "using", using)

        problems = checks.retired_problems(["broken", "default", "empty"])

        assert problems == [
            "parcel_shipped is declared, and retired - its trigger writes nothing. Remove its declaration."
        ]
        assert checks.retired_problems(["default", "broken"]) == problems


class TestTheTasksPassOn:
    def test_send_asks_the_pipeline_as_the_send_task_by_default(self, monkeypatch):
        asked = []
        monkeypatch.setattr(pipeline, "send", lambda pk, triggered_by: asked.append((pk, triggered_by)))

        tasks.send("x")
        tasks.send("y", triggered_by="manual")

        assert asked == [("x", "send"), ("y", "manual")]

    def test_a_shard_is_claimed_by_the_task_running_it(self, monkeypatch):
        asked = []
        monkeypatch.setattr(pipeline, "run_shard", lambda pk, claimed_by: asked.append((pk, claimed_by)))

        tasks.fan_out_shard(1)
        tasks.fan_out_shard.apply(args=[2], task_id="task-2")

        assert asked == [(1, ""), (2, "task-2")]


class TestWhoIsChosen:
    def test_subscribers_are_of_the_notices_kind_and_variant(self, alice, bob):
        subscribe(alice, "parcel_flagged")
        subscribe(bob, "parcel_reminder", "daily")
        notice = raise_notice("parcel_reminder", variant="daily")

        assert list(Subscribers().recipients(notice)) == [bob]
        assert list(Subscribers().recipients(raise_notice("parcel_reminder"))) == []

    def test_a_choice_about_another_kind_doesnt_count(
        self, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        subscribe(bob, "parcel_flagged", enabled=False)
        notice = ship(parcel)

        fan_out(django_capture_on_commit_callbacks)

        assert len(deliveries(notice)) == 2

    def test_a_channel_is_given_the_kind(
        self, monkeypatch, settings, parcel, dispatched, django_capture_on_commit_callbacks
    ):
        given = []

        class Recording(Channel):
            def targets(self, notice, kind, users):
                given.append(kind)
                return []

        monkeypatch.setitem(registered, "recording", None)
        register_channel("recording", Recording)
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "CHANNELS": ("recording",)}
        ship(parcel)

        fan_out(django_capture_on_commit_callbacks)

        assert given == [kinds.get_kind("parcel_shipped")]

    def test_the_audience_is_asked_about_the_notice(
        self, monkeypatch, settings, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        class ByNotice(Audience):
            spans_partitions = True

            def partitions(self, notice):
                return [f"p{notice.subject_id}"]

            def recipients(self, notice):
                return [bob]

        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "PARTITIONS": list, "IN_PARTITION": nullcontext}
        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", audience=ByNotice(), scope=Scope.CROSS_PARTITION):
            fan_out(django_capture_on_commit_callbacks)

        assert list(FanOutShard.objects.values_list("partition", flat=True)) == [f"p{parcel.pk}"]
        assert notice.subject_id == str(parcel.pk)


class TestWritingDeliveries:
    def test_a_target_twice_in_one_go_is_written_once_and_the_rest_still_are(self, parcel, alice, bob):
        notice = ship(parcel)
        first = Target(f"user:{alice.pk}", InboxDelivery, {"user_id": str(alice.pk)})
        second = Target(f"user:{bob.pk}", InboxDelivery, {"user_id": str(bob.pk)})

        written = pipeline.write_deliveries(notice, None, [("inbox", first), ("inbox", first), ("inbox", second)])

        assert [delivery.target_key for delivery in written] == [first.key, second.key]

    def test_a_single_partition_kind_naming_several_says_so(self, parcel):
        notice = ship(parcel)

        class Two(Audience):
            def partitions(self, notice):
                return ["a", "b", "a"]

        with pytest.raises(ValueError) as raised:
            pipeline._single_partition(notice, kinds.Kind(name="k", audience=Two(), policy=Policy.MANDATORY))

        assert (
            str(raised.value) == f"k is single-partition, and its audience named 2 partitions for notice {notice.pk}."
        )


class TestFanningOutEveryNotice:
    # A tie left to the database comes back in whatever order its plan reads the rows, so the tied pair
    # is written both ways round: only an order by key puts the lower one first in both.
    @pytest.mark.parametrize("tied_ids", [(950, 940), (940, 950)])
    def test_oldest_first_then_by_number(self, tied_ids, alice, dispatched, django_capture_on_commit_callbacks):
        now = timezone.now()
        policy = NoticeKindPolicy.objects.get(name="parcel_reminder")
        later = Notice.objects.create(id=900, kind=policy, occurred_at=now)
        earlier = Notice.objects.create(id=901, kind=policy, occurred_at=now - timedelta(hours=1))
        for tied_id in tied_ids:
            Notice.objects.create(id=tied_id, kind=policy, occurred_at=now + timedelta(hours=1))
        tied_low, tied_high = Notice.objects.get(id=940), Notice.objects.get(id=950)

        order = []
        for _ in range(4):
            with django_capture_on_commit_callbacks(execute=True):
                pipeline.fan_out(batch_size=1)
            order.append(Notice.objects.filter(fanned_out_at__isnull=False).exclude(pk__in=order).get().pk)

        assert order == [earlier.pk, later.pk, tied_low.pk, tied_high.pk]

    def test_every_notices_deliveries_and_shards_are_asked_for(
        self, monkeypatch, settings, alice, bob, dispatched, django_capture_on_commit_callbacks
    ):
        class InA(Audience):
            def partitions(self, notice):
                return ["a"]

            def recipients(self, notice):
                return [alice]

        class Both(InA):
            spans_partitions = True

        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "PARTITIONS": lambda: ["a"],
            "IN_PARTITION": nullcontext,
        }
        subscribe(alice, "parcel_flagged")
        flagged = [flag(alice), flag(alice)]
        shipped = [ship(Parcel.objects.create(sender=alice, recipient=bob)) for _ in range(2)]

        with (
            redeclared(monkeypatch, "parcel_shipped", audience=Both(), scope=Scope.CROSS_PARTITION),
            redeclared(monkeypatch, "parcel_flagged", audience=InA()),
        ):
            assert fan_out(django_capture_on_commit_callbacks) == 4

        sends = [args[0] for name, args, kwargs in dispatched if name == "send"]
        assert sorted(sends) == sorted(
            str(pk) for pk in Delivery.objects.filter(notice__in=flagged).values_list("pk", flat=True)
        )
        assert len(sends) == 4
        shards = [args[0] for name, args, kwargs in dispatched if name == "fan_out_shard"]
        assert sorted(shards) == sorted(
            str(pk) for pk in FanOutShard.objects.filter(notice__in=shipped).values_list("pk", flat=True)
        )
        assert len(shards) == 2


class TestWhatIsLogged:
    def test_a_notice_that_cant_be_fanned_out(
        self, monkeypatch, caplog, parcel, dispatched, django_capture_on_commit_callbacks
    ):
        class Broken(Audience):
            def recipients(self, notice):
                raise RuntimeError("down")

        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", audience=Broken()), caplog.at_level(logging.ERROR):
            fan_out(django_capture_on_commit_callbacks)

        assert [record.getMessage() for record in caplog.records] == [
            f"Notice {notice.pk} could not be fanned out; it's set aside to try again."
        ]

    def test_a_failing_shard(self, monkeypatch, caplog, parcel):
        notice = ship(parcel)
        shard = FanOutShard.objects.create(notice=notice, partition="a")
        monkeypatch.setattr(pipeline, "targets", lambda *args: (_ for _ in ()).throw(RuntimeError("down")))

        with caplog.at_level(logging.ERROR):
            pipeline.run_shard(shard.pk)

        assert [record.getMessage() for record in caplog.records] == [f"Shard {shard.pk} of notice {notice.pk} failed."]

    def test_a_failing_send(self, monkeypatch, caplog, shipped):
        delivery = delivery_on(shipped, "inbox")
        monkeypatch.setattr(
            "isik.django.apps.notifications.channels.inbox.InboxChannel.send",
            lambda self, delivery: (_ for _ in ()).throw(RuntimeError("down")),
        )

        with caplog.at_level(logging.ERROR):
            pipeline.send(delivery.pk)

        assert [record.getMessage() for record in caplog.records] == [f"Delivery {delivery.pk} failed on inbox."]


@pytest.fixture
def shipped(parcel, dispatched, django_capture_on_commit_callbacks):
    notice = ship(parcel)
    fan_out(django_capture_on_commit_callbacks)
    dispatched.clear()
    return notice


class TestShardDetails:
    def test_one_claimed_by_nobody_named_says_so(self, parcel):
        shard = FanOutShard.objects.create(notice=ship(parcel), partition="a")

        assert pipeline.run_shard(shard.pk).claimed_by == ""

    def test_finishing_clears_the_last_error(self, parcel):
        shard = FanOutShard.objects.create(notice=ship(parcel), partition="a", last_error="before")

        assert pipeline.run_shard(shard.pk).last_error == ""


class TestSendDetails:
    def test_how_long_it_took(self, monkeypatch, shipped, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}
        clock = iter([100.0, 102.0])
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(clock))

        pipeline.send(delivery_on(shipped, "inbox").pk)

        assert DeliveryAttempt.objects.get().duration_ms == 2000

    def test_the_jitter_is_up_to_a_tenth(self, monkeypatch, settings):
        settings.ISIK_NOTIFICATIONS = {"BACKOFF": (100,)}
        asked = []
        monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: asked.append((low, high)) or 0)

        pipeline.backoff(1)

        assert asked == [(0, 10.0)]


@pytest.mark.django_db(transaction=True)
def test_a_delivery_another_worker_holds_is_skipped(dispatched):
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")
    bob = EmailUser.objects.create(username="bob", email="bob@example.com")
    parcel = Parcel.objects.create(sender=alice, recipient=bob)
    notice = ship(parcel)
    pipeline.fan_out()
    delivery = Delivery.objects.get(notice=notice, channel="inbox")
    held, release = threading.Event(), threading.Event()

    def hold():
        with transaction.atomic():
            Delivery.objects.select_for_update().get(pk=delivery.pk)
            held.set()
            release.wait(5)
        connection.close()

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    try:
        assert pipeline.send(delivery.pk) is None
    finally:
        release.set()
        holder.join(5)


class TestReapingOnlyWhatIsStranded:
    def test_what_is_done_stays_done(self, shipped, parcel, dispatched):
        long_ago = timezone.now() - timedelta(hours=1)
        inbox = delivery_on(shipped, "inbox")
        Delivery.objects.filter(pk=inbox.pk).update(state=DeliveryState.SENT, claimed_at=long_ago)
        Delivery.objects.filter(notice=shipped, channel="email").update(
            next_attempt_at=timezone.now() + timedelta(hours=1)
        )
        finished = FanOutShard.objects.create(
            notice=shipped, partition="a", state=ShardState.FINISHED, claimed_at=long_ago
        )
        FanOutShard.objects.create(notice=shipped, partition="b", next_attempt_at=timezone.now() + timedelta(hours=1))

        assert pipeline.reap_stranded() == {"deliveries": 0, "shards": 0}

        assert Delivery.objects.get(pk=inbox.pk).state == DeliveryState.SENT
        assert FanOutShard.objects.get(pk=finished.pk).state == ShardState.FINISHED
        assert dispatched == []


class TestPruningDetails:
    def test_a_recent_finished_notice_stays(self, shipped, settings):
        Delivery.objects.filter(notice=shipped).update(state=DeliveryState.SENT)
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_RETENTION": timedelta(hours=1)}

        assert pipeline.prune_notices() == 0

    def test_a_notice_goes_with_its_attempts(self, shipped, settings):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "RECORD_ATTEMPTS": "all",
            "NOTICE_RETENTION": timedelta(0),
        }
        for delivery in Delivery.objects.filter(notice=shipped):
            pipeline.send(delivery.pk)

        assert pipeline.prune_notices() == 1
        assert not DeliveryAttempt.objects.exists()


@pytest.fixture
def receiver(monkeypatch):
    receiver = Receiver()
    receiver.options = []
    real = httpx.Client

    def client(**options):
        receiver.options.append({key: value for key, value in options.items() if key != "transport"})
        return real(**options)

    monkeypatch.setattr(webhook_module.httpx, "Client", client)
    monkeypatch.setattr(webhook_module.guard, "transport", lambda: httpx.MockTransport(receiver))
    monkeypatch.setattr(webhook_module.time, "time", lambda: 1761600000.7)
    return receiver


@pytest.fixture
def hooked(alice, dispatched, django_capture_on_commit_callbacks):
    endpoint = get_endpoint_model().objects.create(url="https://hooks.example/in", secret="current")
    get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))
    other = get_endpoint_model().objects.create(url="https://other.example/in", secret="s")
    get_endpoint_kind_model().objects.create(endpoint=other, kind=kind_named("parcel_shipped"))
    notice = flag(alice)
    fan_out(django_capture_on_commit_callbacks)
    dispatched.clear()
    return delivery_on(notice, "webhook"), endpoint


class TestTheWebhookExactly:
    def test_only_endpoints_wanting_the_kind_get_it(self, hooked):
        delivery, endpoint = hooked

        assert Delivery.objects.filter(notice=delivery.notice, channel="webhook").count() == 1

    def test_what_is_sent_and_recorded(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all", "ENDPOINT_TIMEOUT": 3}
        delivery, endpoint = hooked

        pipeline.send(delivery.pk)

        [request] = receiver.requests
        assert receiver.options == [{"timeout": 3, "trust_env": False}]
        assert b", " not in request.content and b": " not in request.content
        assert request.headers["X-Testapp-Signature"].startswith("t=1761600000,v1=")
        attempt = DeliveryAttempt.objects.get()
        signature = webhook_module.signature(endpoint, 1761600000, request.content)
        assert attempt.request == {
            "method": "POST",
            "url": "https://hooks.example/in",
            "headers": {
                "Content-Type": "application/json",
                "Idempotency-Key": str(delivery.pk),
                "X-Testapp-Signature": signature,
            },
            "body": json.loads(request.content),
        }
        assert (attempt.status_code, attempt.succeeded) == (200, True)
        assert attempt.response["body"] == "ok" * 2048

    def test_a_failure_to_connect_still_records_what_was_sent(self, hooked, receiver):
        receiver.error = httpx.ConnectError("refused")

        pipeline.send(hooked[0].pk)

        assert DeliveryAttempt.objects.get().request["url"] == "https://hooks.example/in"

    def test_a_failure_below_the_limit_parks_nothing(self, hooked, receiver):
        receiver.status = 500

        pipeline.send(hooked[0].pk)

        endpoint = get_endpoint_model().objects.get(pk=hooked[1].pk)
        assert (endpoint.consecutive_failures, endpoint.parked_at) == (1, None)

    def test_parking_says_which_model_and_endpoint(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "DISABLE_AFTER": 1}
        receiver.status = 500
        heard = []

        def listen(sender, endpoint, **kwargs):
            heard.append((sender, endpoint.pk))

        endpoint_parked.connect(listen)
        try:
            pipeline.send(hooked[0].pk)
        finally:
            endpoint_parked.disconnect(listen)

        assert heard == [(get_endpoint_model(), hooked[1].pk)]

    def test_an_endpoint_parked_already_isnt_parked_again(self, hooked, receiver, settings):
        delivery, endpoint = hooked
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "DISABLE_AFTER": 1}
        parked_at = timezone.now() - timedelta(days=1)
        get_endpoint_model().objects.filter(pk=endpoint.pk).update(parked_at=parked_at)
        heard = []

        def listen(sender, endpoint, **kwargs):
            heard.append(endpoint)

        endpoint_parked.connect(listen)
        try:
            pipeline.send(delivery.pk)
        finally:
            endpoint_parked.disconnect(listen)

        assert heard == []
        assert get_endpoint_model().objects.get(pk=endpoint.pk).parked_at == parked_at

    def test_it_is_read_and_counted_inside_its_partition(self, hooked, receiver, entered):
        receiver.status = 500

        pipeline.send(in_north(hooked[0]).pk)

        assert entered == ["north", "north"]


class TestTheOtherChannelsInTheirPartition:
    def test_the_inbox(self, shipped, entered):
        pipeline.send(in_north(delivery_on(shipped, "inbox")).pk)

        assert entered == ["north"]

    def test_email(self, shipped, entered, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}

        pipeline.send(in_north(delivery_on(shipped, "email")).pk)

        assert entered == ["north"]
        assert DeliveryAttempt.objects.get().request == {"to": "bob@example.com"}
        assert mail.outbox[0].alternatives[0].mimetype == "text/html"


class TestTheCommandExactly:
    def test_a_kind_described(self):
        kind = kinds.get_kind("parcel_shipped")

        assert describe(kind) == {
            "kind": "parcel_shipped",
            "source": "trigger",
            "model": "testapp.Parcel",
            "trigger": "notify_parcel_shipped",
            "on": "UPDATE",
            "condition": repr(kind.condition),
            "variants": [],
            "audience": "Field('recipient')",
            "policy": "default_on",
            "scope": "single_partition",
            "channels": ["inbox", "email", "webhook"],
            "snapshot": ["status", "recipient", "sender"],
        }

    def test_json_is_indented(self):
        assert run_notices("--format", "json").startswith('{\n  "kinds": [\n    {\n      "kind": ')

    def test_text_names_each_kind_once_and_joins_drift(self):
        with pgtrigger.ignore("isik_notifications.NoticeKindPolicy:protect_policy_delete"):
            NoticeKindPolicy.objects.filter(name__in=["parcel_reminder", "parcel_flagged"]).delete()

        listed = run_notices()

        assert "  kind:" not in listed
        assert "Declared, with no policy row - run migrate: parcel_flagged, parcel_reminder\n" in listed

    def test_retiring_one_retires_it_alone(self):
        run_notices("--retire", "parcel_shipped")

        assert list(NoticeKindPolicy.objects.filter(retired_at__isnull=False).values_list("name", flat=True)) == [
            "parcel_shipped"
        ]


def test_unsubscribing_from_a_kind_leaves_the_others(alice):
    subscribe(alice, "parcel_flagged")
    subscribe(alice, "parcel_shipped", enabled=False)

    unsubscribe(alice, "parcel_flagged")

    assert list(get_subscription_model().objects.values_list("kind__name", flat=True)) == ["parcel_shipped"]


def test_the_flush_is_handed_the_test_it_ends(monkeypatch):
    given = []
    monkeypatch.setattr(TransactionTestCase, "_fixture_teardown", lambda self: given.append(self))
    test = TransactionTestCase("__init__")

    with flushing_through_truncate_protection():
        test._fixture_teardown()

    assert given == [test]


class TestWhatAClaimReadsAndHolds:
    """Each claim reads the rows its work needs in its own query, and holds only the row it claims - a kind's
    policy row held as well would make a retire skip every notice of it, and a send skip every delivery."""

    def claiming(self, queries, table):
        [sql] = [
            query["sql"] for query in queries if f'FROM "{table}"' in query["sql"] and "SKIP LOCKED" in query["sql"]
        ]
        return sql

    def test_a_fan_out(self, parcel, dispatched):
        ship(parcel)

        with CaptureQueriesContext(connection) as queries:
            pipeline.fan_out()

        sql = self.claiming(queries, "isik_notice")
        assert 'JOIN "isik_notice_kind_policy"' in sql
        assert sql.endswith('FOR UPDATE OF "isik_notice" SKIP LOCKED')

    def test_a_send(self, shipped):
        delivery = delivery_on(shipped, "inbox")

        with CaptureQueriesContext(connection) as queries:
            pipeline.send(delivery.pk)

        sql = self.claiming(queries, "isik_delivery")
        assert ('JOIN "isik_notice"' in sql, 'JOIN "isik_notice_kind_policy"' in sql) == (True, True)
        assert sql.endswith('FOR UPDATE OF "isik_delivery" SKIP LOCKED')
