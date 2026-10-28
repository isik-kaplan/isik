"""Fanning notices out, sending deliveries on each channel, and the housekeeping - against the real database."""

import dataclasses
import hashlib
import hmac
import json
import threading
import types
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest
from django.core import mail
from django.db import connection, transaction
from django.utils import timezone

from isik.django.apps.notifications import kinds as kinds_module
from isik.django.apps.notifications import pipeline
from isik.django.apps.notifications.audience.helpers import subscribe
from isik.django.apps.notifications.audience.swapping import (
    get_endpoint_kind_model,
    get_endpoint_model,
    get_inbox_item_model,
)
from isik.django.apps.notifications.audiences import Audience
from isik.django.apps.notifications.channels import Outcome, register_channel, registered
from isik.django.apps.notifications.channels.webhook import WebhookChannel
from isik.django.apps.notifications.models import (
    Delivery,
    DeliveryAttempt,
    DeliveryState,
    FanOutShard,
    Notice,
    Policy,
    Scope,
    ShardState,
)
from isik.django.apps.notifications.signals import endpoint_parked
from tests.testapp.models import EmailUser, Parcel


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_jitter(monkeypatch):
    monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: 0)


def ship(parcel):
    parcel.status = "shipped"
    parcel.save()
    return Notice.objects.get(kind_id="parcel_shipped", subject_id=str(parcel.pk))


def flag(sender, status="lost"):
    parcel = Parcel.objects.create(sender=sender, status=status)
    return Notice.objects.get(kind_id="parcel_flagged", subject_id=str(parcel.pk))


def fan_out(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        return pipeline.fan_out()


def deliveries(notice):
    return sorted(Delivery.objects.filter(notice=notice).values_list("channel", "target_key"))


@contextmanager
def redeclared(monkeypatch, name, **changes):
    """The kind `name` declared with `changes` for the duration."""
    replaced = [dataclasses.replace(kind, **changes) if kind.name == name else kind for kind in kinds_module.declared]
    monkeypatch.setattr(kinds_module, "declared", replaced)
    yield


class TwoPartitions(Audience):
    spans_partitions = True

    def __init__(self, recipients=()):
        self.users = recipients

    def partitions(self, notice):
        return ["north", "south", "north"]

    def recipients(self, notice):
        return self.users


@pytest.fixture
def partitioned(settings):
    entered = []

    @contextmanager
    def in_partition(key):
        entered.append(key)
        yield

    settings.ISIK_NOTIFICATIONS = {
        "PROJECT_NAME": "Testapp",
        "PARTITIONS": lambda: ["north", "south"],
        "IN_PARTITION": in_partition,
    }
    return entered


class TestFanningOut:
    def test_a_default_on_kind_reaches_its_audience_on_every_channel(
        self, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        notice = ship(parcel)

        assert fan_out(django_capture_on_commit_callbacks) == 1

        assert deliveries(notice) == [("email", "email:bob@example.com"), ("inbox", f"user:{bob.pk}")]
        notice.refresh_from_db()
        assert notice.fanned_out_at is not None
        sent = sorted(Delivery.objects.filter(notice=notice).values_list("pk", flat=True))
        assert sorted(args[0] for name, args, kwargs in dispatched) == sorted(str(pk) for pk in sent)
        assert {name for name, args, kwargs in dispatched} == {"send"}

    def test_the_children_name_the_target(self, parcel, bob, dispatched, django_capture_on_commit_callbacks):
        notice = ship(parcel)
        fan_out(django_capture_on_commit_callbacks)

        inbox = Delivery.objects.get(notice=notice, channel="inbox")
        email = Delivery.objects.get(notice=notice, channel="email")
        assert inbox.inbox.user_id == str(bob.pk)
        assert (email.email.address, email.email.user_id) == ("bob@example.com", str(bob.pk))
        assert inbox.partition is None

    def test_someone_opting_out_of_a_default_on_kind_isnt_reached(
        self, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        subscribe(bob, "parcel_shipped", enabled=False)
        notice = ship(parcel)

        fan_out(django_capture_on_commit_callbacks)

        assert deliveries(notice) == []
        notice.refresh_from_db()
        assert notice.fanned_out_at is not None

    def test_a_default_off_kind_reaches_only_those_opting_into_its_variant(
        self, alice, dispatched, django_capture_on_commit_callbacks
    ):
        subscribe(alice, "parcel_flagged", "lost")
        lost, damaged = flag(alice, "lost"), flag(alice, "damaged")

        fan_out(django_capture_on_commit_callbacks)

        assert [channel for channel, key in deliveries(lost)] == ["email", "inbox"]
        assert deliveries(damaged) == []

    def test_opting_in_to_every_variant_covers_each(self, alice, dispatched, django_capture_on_commit_callbacks):
        subscribe(alice, "parcel_flagged")
        damaged = flag(alice, "damaged")

        fan_out(django_capture_on_commit_callbacks)

        assert len(deliveries(damaged)) == 2

    def test_a_mandatory_kind_ignores_subscriptions(
        self, monkeypatch, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        subscribe(bob, "parcel_shipped", enabled=False)
        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", policy=Policy.MANDATORY):
            fan_out(django_capture_on_commit_callbacks)

        assert len(deliveries(notice)) == 2

    def test_an_empty_audience_reaches_nobody(self, alice, dispatched, django_capture_on_commit_callbacks):
        parcel = Parcel.objects.create(sender=alice)
        notice = ship(parcel)

        fan_out(django_capture_on_commit_callbacks)

        assert deliveries(notice) == []

    def test_a_kinds_own_channels(self, monkeypatch, parcel, bob, dispatched, django_capture_on_commit_callbacks):
        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", channels=("inbox",)):
            fan_out(django_capture_on_commit_callbacks)

        assert deliveries(notice) == [("inbox", f"user:{bob.pk}")]

    def test_endpoints_wanting_the_variant_get_it_whoever_the_audience_is(
        self, alice, dispatched, django_capture_on_commit_callbacks
    ):
        endpoints, wanted = get_endpoint_model().objects, get_endpoint_kind_model().objects
        lost_only = endpoints.create(url="https://a.example/hook", secret="s")
        every = endpoints.create(url="https://b.example/hook", secret="s")
        inactive = endpoints.create(url="https://c.example/hook", secret="s", is_active=False)
        parked = endpoints.create(url="https://d.example/hook", secret="s", parked_at=timezone.now())
        wanted.create(endpoint=lost_only, kind_id="parcel_flagged", variant="lost")
        for endpoint in (every, inactive, parked):
            wanted.create(endpoint=endpoint, kind_id="parcel_flagged")
        lost, damaged = flag(alice, "lost"), flag(alice, "damaged")

        fan_out(django_capture_on_commit_callbacks)

        assert deliveries(lost) == sorted(
            [("webhook", f"endpoint:{lost_only.pk}"), ("webhook", f"endpoint:{every.pk}")]
        )
        assert deliveries(damaged) == [("webhook", f"endpoint:{every.pk}")]
        assert Delivery.objects.get(notice=damaged).webhook.endpoint_id == str(every.pk)

    def test_an_owned_endpoint_gets_only_its_owners_notices(
        self, monkeypatch, settings, alice, dispatched, django_capture_on_commit_callbacks
    ):
        # The test project's endpoints have no owner; their URL stands in for one, the audience naming it by
        # what the notice says.
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "ENDPOINT_OWNER_FIELD": "url"}
        endpoints, wanted = get_endpoint_model().objects, get_endpoint_kind_model().objects
        mine = endpoints.create(url="https://lost.example/hook", secret="s")
        theirs = endpoints.create(url="https://damaged.example/hook", secret="s")
        # Keyed apart from the endpoints, so a target is named by its endpoint, never by the row wanting it.
        for offset, endpoint in enumerate((mine, theirs), start=1000):
            wanted.create(pk=endpoint.pk + offset, endpoint=endpoint, kind_id="parcel_flagged")

        class ToAlicesHook(Audience):
            def recipients(self, notice):
                return [types.SimpleNamespace(pk=f"https://{notice.snapshot['status']}.example/hook")]

        lost = flag(alice, "lost")
        # parcel_flagged is default-off, and nobody subscribed: the endpoint's own kinds are its choice.
        with redeclared(monkeypatch, "parcel_flagged", audience=ToAlicesHook()):
            fan_out(django_capture_on_commit_callbacks)

        assert deliveries(lost) == [("webhook", f"endpoint:{mine.pk}")]

    def test_oldest_first_and_no_more_than_a_batch(self, alice, dispatched, django_capture_on_commit_callbacks):
        first, second = flag(alice), flag(alice)

        with django_capture_on_commit_callbacks(execute=True):
            assert pipeline.fan_out(batch_size=1) == 1

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.fanned_out_at is not None
        assert second.fanned_out_at is None

    def test_a_notice_that_fails_is_set_aside_as_a_shard_and_the_rest_go_on(
        self, monkeypatch, parcel, alice, dispatched, django_capture_on_commit_callbacks
    ):
        class Broken(Audience):
            def recipients(self, notice):
                raise RuntimeError("down")

        broken, fine = ship(parcel), flag(alice)

        with redeclared(monkeypatch, "parcel_shipped", audience=Broken()):
            assert fan_out(django_capture_on_commit_callbacks) == 2

        broken.refresh_from_db()
        fine.refresh_from_db()
        assert broken.fanned_out_at is not None
        assert fine.fanned_out_at is not None
        [shard] = FanOutShard.objects.filter(notice=broken)
        assert (shard.state, shard.partition, shard.attempt_count, shard.last_error) == (
            ShardState.PENDING,
            None,
            1,
            "RuntimeError: down",
        )
        assert shard.next_attempt_at > timezone.now() + timedelta(seconds=59)
        assert not FanOutShard.objects.filter(notice=fine).exists()

    def test_a_set_aside_notice_is_delivered_by_its_shard_once_it_can_be(
        self, monkeypatch, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        class DownOnce(Audience):
            failed = False

            def recipients(self, notice):
                if not DownOnce.failed:
                    DownOnce.failed = True
                    raise RuntimeError("down")
                return [bob]

        notice = ship(parcel)
        with redeclared(monkeypatch, "parcel_shipped", audience=DownOnce()):
            fan_out(django_capture_on_commit_callbacks)
        shard = FanOutShard.objects.get(notice=notice)
        FanOutShard.objects.filter(pk=shard.pk).update(next_attempt_at=timezone.now())

        with django_capture_on_commit_callbacks(execute=True):
            assert pipeline.run_shard(shard.pk).state == ShardState.FINISHED

        assert deliveries(notice) == [("email", "email:bob@example.com"), ("inbox", f"user:{bob.pk}")]

    def test_a_kind_no_longer_declared_is_stamped_and_reaches_nobody(
        self, monkeypatch, parcel, dispatched, django_capture_on_commit_callbacks
    ):
        notice = ship(parcel)
        monkeypatch.setattr(
            kinds_module, "declared", [kind for kind in kinds_module.declared if kind.name != "parcel_shipped"]
        )

        fan_out(django_capture_on_commit_callbacks)

        notice.refresh_from_db()
        assert notice.fanned_out_at is not None
        assert deliveries(notice) == []

    def test_a_single_partition_kind_whose_audience_names_several_is_set_aside(
        self, monkeypatch, parcel, dispatched, django_capture_on_commit_callbacks
    ):
        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", audience=TwoPartitions()):
            fan_out(django_capture_on_commit_callbacks)

        assert FanOutShard.objects.get(notice=notice).last_error.startswith("ValueError: parcel_shipped is single")
        assert deliveries(notice) == []

    def test_a_single_partition_kind_is_read_inside_its_partition(
        self, monkeypatch, partitioned, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        class InNorth(Audience):
            def partitions(self, notice):
                return ["north"]

            def recipients(self, notice):
                return [bob]

        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", audience=InNorth()):
            fan_out(django_capture_on_commit_callbacks)

        assert partitioned == ["north"]
        assert set(Delivery.objects.filter(notice=notice).values_list("partition", flat=True)) == {"north"}

    def test_a_cross_partition_kind_scatters_into_one_shard_per_partition(
        self, monkeypatch, partitioned, parcel, dispatched, django_capture_on_commit_callbacks
    ):
        notice = ship(parcel)

        with redeclared(monkeypatch, "parcel_shipped", audience=TwoPartitions(), scope=Scope.CROSS_PARTITION):
            fan_out(django_capture_on_commit_callbacks)

        shards = list(FanOutShard.objects.filter(notice=notice).order_by("partition"))
        assert [shard.partition for shard in shards] == ["north", "south"]
        assert deliveries(notice) == []
        assert dispatched == [("fan_out_shard", (str(shard.pk),), {}) for shard in shards]
        notice.refresh_from_db()
        assert notice.fanned_out_at is not None


@pytest.mark.django_db(transaction=True)
def test_a_notice_another_fan_out_holds_is_skipped(dispatched):
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")
    held_notice = flag(alice)
    held, release = threading.Event(), threading.Event()

    def hold():
        with transaction.atomic():
            Notice.objects.select_for_update().get(pk=held_notice.pk)
            held.set()
            release.wait(5)
        connection.close()

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    try:
        assert pipeline.fan_out() == 0
    finally:
        release.set()
        holder.join(5)
    assert pipeline.fan_out() == 1


class TestShards:
    @pytest.fixture
    def shard(self, monkeypatch, partitioned, parcel, bob, dispatched, django_capture_on_commit_callbacks):
        notice = ship(parcel)
        with redeclared(monkeypatch, "parcel_shipped", audience=TwoPartitions([bob]), scope=Scope.CROSS_PARTITION):
            fan_out(django_capture_on_commit_callbacks)
            dispatched.clear()
            yield FanOutShard.objects.get(notice=notice, partition="south")

    def test_running_one_writes_its_partitions_deliveries(
        self, shard, partitioned, bob, dispatched, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            ran = pipeline.run_shard(shard.pk, claimed_by="task-1")

        assert ran.state == ShardState.FINISHED
        assert ran.finished_at is not None
        assert (ran.attempt_count, ran.claimed_by) == (1, "task-1")
        assert partitioned[-1] == "south"
        written = Delivery.objects.filter(notice=shard.notice)
        assert set(written.values_list("partition", flat=True)) == {"south"}
        assert sorted(args[0] for name, args, kwargs in dispatched) == sorted(
            str(pk) for pk in written.values_list("pk", flat=True)
        )

    def test_one_not_due_or_not_pending_isnt_claimed(self, shard):
        FanOutShard.objects.filter(pk=shard.pk).update(next_attempt_at=timezone.now() + timedelta(hours=1))
        assert pipeline.run_shard(shard.pk) is None
        FanOutShard.objects.filter(pk=shard.pk).update(next_attempt_at=timezone.now(), state=ShardState.FINISHED)
        assert pipeline.run_shard(shard.pk) is None

    def test_a_failing_one_waits_then_is_abandoned(self, monkeypatch, shard, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "MAX_ATTEMPTS": 2, "BACKOFF": (60,)}
        monkeypatch.setattr(pipeline, "targets", lambda *args: (_ for _ in ()).throw(RuntimeError("down")))

        failed = pipeline.run_shard(shard.pk)

        assert failed.state == ShardState.PENDING
        assert failed.last_error == "RuntimeError: down"
        assert timedelta(seconds=59) < failed.next_attempt_at - timezone.now() <= timedelta(seconds=60)
        FanOutShard.objects.filter(pk=shard.pk).update(next_attempt_at=timezone.now())
        assert pipeline.run_shard(shard.pk).state == ShardState.ABANDONED

    def test_a_kind_no_longer_declared_finishes_with_nothing(self, monkeypatch, shard):
        monkeypatch.setattr(kinds_module, "declared", [])

        assert pipeline.run_shard(shard.pk).state == ShardState.FINISHED
        assert not Delivery.objects.filter(notice=shard.notice).exists()


def test_writing_deliveries_twice_writes_them_once(parcel, bob):
    notice = ship(parcel)
    kind = kinds_module.get_kind("parcel_shipped")
    reached = pipeline.targets(notice, kind, None)

    assert len(pipeline.write_deliveries(notice, None, reached)) == 2
    assert pipeline.write_deliveries(notice, None, reached + reached) == []


class TestBackoff:
    def test_each_failure_waits_its_entry_then_the_last(self, settings):
        settings.ISIK_NOTIFICATIONS = {"BACKOFF": (10, 20)}

        assert [pipeline.backoff(n) for n in (1, 2, 3)] == [timedelta(seconds=s) for s in (10, 20, 20)]

    def test_jitter_adds_up_to_a_tenth(self, monkeypatch, settings):
        settings.ISIK_NOTIFICATIONS = {"BACKOFF": (100,)}
        monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: (low, high)[1])

        assert pipeline.backoff(1) == timedelta(seconds=110)


@pytest.fixture
def shipped(parcel, dispatched, django_capture_on_commit_callbacks):
    notice = ship(parcel)
    fan_out(django_capture_on_commit_callbacks)
    dispatched.clear()
    return notice


def delivery_on(notice, channel):
    return Delivery.objects.get(notice=notice, channel=channel)


class TestSendingToTheInbox:
    def test_it_lands_once(self, shipped, bob):
        delivery = delivery_on(shipped, "inbox")

        sent = pipeline.send(delivery.pk)

        assert sent.state == DeliveryState.SENT
        assert (sent.attempt_count, sent.sent_at is not None) == (1, True)
        [item] = get_inbox_item_model().objects.filter(user=bob)
        assert (item.kind_id, item.variant, item.subject_label, item.subject_id) == (
            "parcel_shipped",
            None,
            "testapp.Parcel",
            shipped.subject_id,
        )
        assert item.payload == {"data": shipped.snapshot, "previous": shipped.previous}
        assert item.delivery_id == delivery.pk
        Delivery.objects.filter(pk=delivery.pk).update(state=DeliveryState.PENDING)
        pipeline.send(delivery.pk)
        assert get_inbox_item_model().objects.filter(user=bob).count() == 1

    def test_one_already_sent_or_not_due_isnt_claimed(self, shipped):
        delivery = delivery_on(shipped, "inbox")
        Delivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now() + timedelta(hours=1))
        assert pipeline.send(delivery.pk) is None
        Delivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now(), state=DeliveryState.SENT)
        assert pipeline.send(delivery.pk) is None

    def test_a_success_records_no_attempt_by_default(self, shipped):
        pipeline.send(delivery_on(shipped, "inbox").pk)

        assert not DeliveryAttempt.objects.exists()


class TestSendingEmail:
    def test_it_renders_the_kinds_templates(self, shipped):
        delivery = delivery_on(shipped, "email")

        assert pipeline.send(delivery.pk).state == DeliveryState.SENT

        [message] = mail.outbox
        assert message.subject == "Parcel shipped"
        assert message.body == "Your parcel is shipped (was packed).\n"
        assert message.to == ["bob@example.com"]
        assert message.alternatives[0].content == "<p>Your parcel is shipped.</p>\n"
        assert message.extra_headers["Message-ID"].startswith(f"<{delivery.pk}@")

    def test_html_is_optional(self, alice, dispatched, django_capture_on_commit_callbacks):
        subscribe(alice, "parcel_flagged")
        notice = flag(alice)
        fan_out(django_capture_on_commit_callbacks)
        delivery = delivery_on(notice, "email")

        pipeline.send(delivery.pk)

        [message] = mail.outbox
        assert (message.subject, message.body, message.alternatives) == ("Parcel lost", f"Delivery {delivery.pk}\n", [])

    def test_a_project_sends_its_own(self, shipped, settings):
        sent = []
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "EMAIL_SENDER": lambda delivery, notice, address: sent.append((delivery.pk, notice.pk, address)),
        }
        delivery = delivery_on(shipped, "email")

        pipeline.send(delivery.pk)

        assert sent == [(delivery.pk, shipped.pk, "bob@example.com")]
        assert mail.outbox == []

    def test_a_project_picks_the_address_or_none(
        self, parcel, settings, dispatched, django_capture_on_commit_callbacks
    ):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "EMAIL_ADDRESS": lambda user: f"{user.username}@work.example",
        }
        notice = ship(parcel)
        fan_out(django_capture_on_commit_callbacks)

        assert ("email", "email:bob@work.example") in deliveries(notice)

    def test_a_user_with_no_address_gets_no_email(self, parcel, bob, dispatched, django_capture_on_commit_callbacks):
        EmailUser.objects.filter(pk=bob.pk).update(email="")
        notice = ship(parcel)

        fan_out(django_capture_on_commit_callbacks)

        assert [channel for channel, key in deliveries(notice)] == ["inbox"]


class Receiver:
    """
    A webhook receiver behind an httpx MockTransport: answers `status` with `text` and `headers` - or with
    the body sent in `chunks`, when they're set - or raises `error`.
    """

    def __init__(self, status=200, error=None):
        self.status, self.error, self.requests = status, error, []
        self.text, self.headers, self.chunks = "ok" * 5000, {"X-Answer": "yes"}, None

    def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.chunks is not None:
            return httpx.Response(self.status, content=iter(self.chunks), headers=self.headers)
        return httpx.Response(self.status, text=self.text, headers=self.headers)


@pytest.fixture
def receiver(monkeypatch):
    receiver = Receiver()
    monkeypatch.setattr(
        "isik.django.apps.notifications.channels.guard.transport", lambda: httpx.MockTransport(receiver)
    )
    return receiver


@pytest.fixture
def hooked(alice, dispatched, django_capture_on_commit_callbacks):
    endpoint = get_endpoint_model().objects.create(url="https://hooks.example/in", secret="current")
    get_endpoint_kind_model().objects.create(endpoint=endpoint, kind_id="parcel_flagged")
    notice = flag(alice)
    fan_out(django_capture_on_commit_callbacks)
    dispatched.clear()
    return delivery_on(notice, "webhook"), endpoint


class TestSendingWebhooks:
    def test_it_posts_the_notice_signed_with_its_key(self, hooked, receiver):
        delivery, endpoint = hooked

        assert pipeline.send(delivery.pk).state == DeliveryState.SENT

        [request] = receiver.requests
        assert str(request.url) == "https://hooks.example/in"
        assert request.headers["Idempotency-Key"] == str(delivery.pk)
        assert request.headers["Content-Type"] == "application/json"
        timestamp, signature = request.headers["X-Testapp-Signature"].split(",")
        expected = hmac.new(b"current", f"{timestamp[2:]}.".encode() + request.content, hashlib.sha256).hexdigest()
        assert signature == f"v1={expected}"
        body = json.loads(request.content)
        notice = delivery.notice
        assert body == {
            "id": str(delivery.pk),
            "kind": "parcel_flagged",
            "variant": "lost",
            "occurred_at": body["occurred_at"],
            "subject": {"label": "testapp.Parcel", "id": notice.subject_id, "partition": None},
            "data": notice.snapshot,
            "previous": None,
        }

    def test_a_secret_being_rotated_out_signs_too(self, hooked, receiver):
        delivery, endpoint = hooked
        get_endpoint_model().objects.filter(pk=endpoint.pk).update(secret_previous="old")

        pipeline.send(delivery.pk)

        [request] = receiver.requests
        timestamp, current, previous = request.headers["X-Testapp-Signature"].split(",")
        signed = f"{timestamp[2:]}.".encode() + request.content
        assert previous == f"v1={hmac.new(b'old', signed, hashlib.sha256).hexdigest()}"
        assert current == f"v1={hmac.new(b'current', signed, hashlib.sha256).hexdigest()}"

    def test_the_header_is_named_by_the_setting(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "SIGNATURE": {"header": "Signature"}}

        pipeline.send(hooked[0].pk)

        assert "Signature" in receiver.requests[0].headers
        assert "X-Testapp-Signature" not in receiver.requests[0].headers

    def test_a_server_error_is_tried_again_later(self, hooked, receiver, dispatched):
        delivery, endpoint = hooked
        receiver.status = 503

        failed = pipeline.send(delivery.pk)

        assert failed.state == DeliveryState.PENDING
        assert failed.next_attempt_at - timezone.now() > timedelta(seconds=59)
        [attempt] = DeliveryAttempt.objects.all()
        assert (attempt.attempt_no, attempt.status_code, attempt.succeeded) == (1, 503, False)
        assert attempt.error == "The endpoint answered 503."
        assert attempt.triggered_by == "send"
        assert attempt.response["headers"]["x-answer"] == "yes"
        assert len(attempt.response["body"]) == 4096
        assert attempt.request["headers"]["Idempotency-Key"] == str(delivery.pk)
        endpoint.refresh_from_db()
        assert endpoint.consecutive_failures == 1

    @pytest.mark.parametrize("status", [408, 425, 429])
    def test_a_client_error_saying_not_now_is_tried_again(self, hooked, receiver, status):
        receiver.status = status

        assert pipeline.send(hooked[0].pk).state == DeliveryState.PENDING

    @pytest.mark.parametrize("status", [400, 404, 410])
    def test_any_other_client_error_is_abandoned(self, hooked, receiver, status):
        receiver.status = status

        assert pipeline.send(hooked[0].pk).state == DeliveryState.ABANDONED

    def test_no_answer_is_tried_again(self, hooked, receiver):
        receiver.error = httpx.ConnectTimeout("too slow")

        failed = pipeline.send(hooked[0].pk)

        assert failed.state == DeliveryState.PENDING
        [attempt] = DeliveryAttempt.objects.all()
        assert (attempt.status_code, attempt.response, attempt.error) == (None, None, "ConnectTimeout: too slow")

    def test_a_success_clears_the_endpoints_failures(self, hooked, receiver):
        delivery, endpoint = hooked
        get_endpoint_model().objects.filter(pk=endpoint.pk).update(consecutive_failures=3)

        pipeline.send(delivery.pk)

        endpoint.refresh_from_db()
        assert endpoint.consecutive_failures == 0

    def test_failures_in_a_row_park_the_endpoint_and_say_so(self, hooked, receiver, settings):
        delivery, endpoint = hooked
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "DISABLE_AFTER": 2}
        get_endpoint_model().objects.filter(pk=endpoint.pk).update(consecutive_failures=1)
        receiver.status = 500
        parked = []

        def heard(sender, endpoint, **kwargs):
            parked.append(endpoint)

        endpoint_parked.connect(heard)
        try:
            pipeline.send(delivery.pk)
        finally:
            endpoint_parked.disconnect(heard)

        endpoint.refresh_from_db()
        assert endpoint.parked_at is not None
        assert [each.pk for each in parked] == [endpoint.pk]

    def test_a_parked_endpoint_isnt_parked_again(self, hooked, receiver, settings):
        delivery, endpoint = hooked
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "DISABLE_AFTER": 1}
        receiver.status = 500
        pipeline.send(delivery.pk)
        endpoint.refresh_from_db()
        assert endpoint.parked_at is not None

        endpoint.unpark()
        endpoint.refresh_from_db()
        assert (endpoint.parked_at, endpoint.consecutive_failures) == (None, 0)

    def test_an_endpoint_gone_or_parked_is_abandoned_unsent(self, hooked, receiver):
        delivery, endpoint = hooked
        get_endpoint_model().objects.filter(pk=endpoint.pk).update(parked_at=timezone.now())

        assert pipeline.send(delivery.pk).state == DeliveryState.ABANDONED

        assert receiver.requests == []
        assert DeliveryAttempt.objects.get().error == "The endpoint is parked - unpark it first."
        get_endpoint_model().objects.filter(pk=endpoint.pk).delete()
        assert WebhookChannel()._endpoint(delivery) == (None, "The endpoint no longer exists.")

    def test_an_inactive_endpoint_is_abandoned_unsent(self, hooked, receiver):
        delivery, endpoint = hooked
        get_endpoint_model().objects.filter(pk=endpoint.pk).update(is_active=False)

        assert WebhookChannel()._endpoint(delivery) == (None, "The endpoint is inactive.")


class TestTriesAndRecords:
    def test_the_last_try_abandons_it(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "MAX_ATTEMPTS": 2}
        receiver.status = 500
        delivery = hooked[0]

        assert pipeline.send(delivery.pk).state == DeliveryState.PENDING
        Delivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
        assert pipeline.send(delivery.pk).state == DeliveryState.ABANDONED
        assert list(DeliveryAttempt.objects.order_by("attempt_no").values_list("attempt_no", flat=True)) == [1, 2]

    def test_a_retry_is_asked_for_when_its_due(self, hooked, receiver, dispatched, django_capture_on_commit_callbacks):
        receiver.status = 500

        with django_capture_on_commit_callbacks(execute=True):
            failed = pipeline.send(hooked[0].pk)

        assert dispatched == [("send", (str(failed.pk),), {"eta": failed.next_attempt_at})]

    def test_a_channel_raising_is_a_failure_to_try_again(self, monkeypatch, shipped):
        monkeypatch.setattr(
            "isik.django.apps.notifications.channels.inbox.InboxChannel.send",
            lambda self, delivery: (_ for _ in ()).throw(RuntimeError("inbox down")),
        )

        failed = pipeline.send(delivery_on(shipped, "inbox").pk)

        assert failed.state == DeliveryState.PENDING
        assert DeliveryAttempt.objects.get().error == "RuntimeError: inbox down"

    def test_every_attempt_is_recorded_when_asked(self, shipped, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}

        pipeline.send(delivery_on(shipped, "inbox").pk, triggered_by="manual")

        attempt = DeliveryAttempt.objects.get()
        assert (attempt.succeeded, attempt.triggered_by, attempt.request, attempt.response) == (
            True,
            "manual",
            {},
            None,
        )
        assert attempt.duration_ms >= 0

    def test_none_is_recorded_when_asked(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "none"}
        receiver.status = 500

        pipeline.send(hooked[0].pk)

        assert not DeliveryAttempt.objects.exists()

    def test_the_redactor_sees_what_is_recorded(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "REDACTOR": lambda value: {key: "[redacted]" for key in value},
        }
        receiver.status = 500

        pipeline.send(hooked[0].pk)

        attempt = DeliveryAttempt.objects.get()
        assert set(attempt.request.values()) == {"[redacted]"}
        assert set(attempt.response.values()) == {"[redacted]"}


class TestReapingTheStranded:
    def test_a_delivery_or_shard_held_too_long_goes_back_and_is_asked_for(
        self, monkeypatch, partitioned, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        notice = ship(parcel)
        with redeclared(monkeypatch, "parcel_shipped", audience=TwoPartitions([bob]), scope=Scope.CROSS_PARTITION):
            fan_out(django_capture_on_commit_callbacks)
        long_ago = timezone.now() - timedelta(hours=1)
        north, south = FanOutShard.objects.filter(notice=notice).order_by("partition")
        FanOutShard.objects.filter(pk=north.pk).update(
            state=ShardState.CLAIMED, claimed_at=long_ago, next_attempt_at=long_ago
        )
        FanOutShard.objects.filter(pk=south.pk).update(state=ShardState.CLAIMED, claimed_at=timezone.now())
        other = flag(bob)
        dispatched.clear()

        assert pipeline.reap_stranded() == {"deliveries": 0, "shards": 1}

        assert FanOutShard.objects.get(pk=north.pk).state == ShardState.PENDING
        assert FanOutShard.objects.get(pk=south.pk).state == ShardState.CLAIMED
        assert dispatched == [("fan_out_shard", (str(north.pk),), {}), ("fan_out", (), {})]
        assert other.fanned_out_at is None

    def test_a_sending_delivery_held_too_long_is_sent_again(self, shipped, dispatched):
        inbox, email = delivery_on(shipped, "inbox"), delivery_on(shipped, "email")
        long_ago = timezone.now() - timedelta(hours=1)
        Delivery.objects.filter(pk=inbox.pk).update(
            state=DeliveryState.SENDING, claimed_at=long_ago, next_attempt_at=long_ago
        )
        Delivery.objects.filter(pk=email.pk).update(state=DeliveryState.SENDING, claimed_at=timezone.now())

        assert pipeline.reap_stranded() == {"deliveries": 1, "shards": 0}

        assert dispatched == [("send", (str(inbox.pk),), {"triggered_by": "reap_stranded"})]

    def test_nothing_waiting_asks_for_nothing(self, db, dispatched):
        assert pipeline.reap_stranded() == {"deliveries": 0, "shards": 0}
        assert dispatched == []


class TestPruning:
    def test_old_attempts_go(self, hooked, receiver, settings):
        receiver.status = 500
        pipeline.send(hooked[0].pk)
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "ATTEMPT_RETENTION": timedelta(0)}

        assert pipeline.prune_attempts() == 1
        assert not DeliveryAttempt.objects.exists()

    def test_recent_attempts_stay(self, hooked, receiver):
        receiver.status = 500
        pipeline.send(hooked[0].pk)

        assert pipeline.prune_attempts() == 0

    def test_no_retention_keeps_everything(self, settings):
        settings.ISIK_NOTIFICATIONS = {"ATTEMPT_RETENTION": None, "NOTICE_RETENTION": None}

        assert (pipeline.prune_attempts(), pipeline.prune_notices()) == (0, 0)

    def test_old_finished_notices_go_with_their_deliveries(self, shipped, settings):
        for delivery in Delivery.objects.filter(notice=shipped):
            pipeline.send(delivery.pk)
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_RETENTION": timedelta(0)}

        assert pipeline.prune_notices() == 1
        assert not Notice.objects.exists()
        assert not Delivery.objects.exists()

    def test_a_notice_still_waiting_or_sending_stays(self, shipped, alice, settings):
        waiting = flag(alice)
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_RETENTION": timedelta(0)}

        assert pipeline.prune_notices() == 0
        assert set(Notice.objects.values_list("pk", flat=True)) == {shipped.pk, waiting.pk}

    def test_a_notice_with_an_unfinished_shard_stays(
        self, monkeypatch, partitioned, parcel, bob, dispatched, django_capture_on_commit_callbacks, settings
    ):
        notice = ship(parcel)
        with redeclared(monkeypatch, "parcel_shipped", audience=TwoPartitions([bob]), scope=Scope.CROSS_PARTITION):
            fan_out(django_capture_on_commit_callbacks)
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_RETENTION": timedelta(0)}

        assert pipeline.prune_notices() == 0
        FanOutShard.objects.filter(notice=notice).update(state=ShardState.FINISHED)
        assert pipeline.prune_notices() == 1


class TestTheBacklogsAge:
    def test_none_when_nothing_waits(self, db):
        assert pipeline.oldest_unfanned_notice_age() is None

    def test_the_oldest_waiting_ones(self, alice):
        oldest = flag(alice)

        age = pipeline.oldest_unfanned_notice_age()

        assert timedelta(0) <= age < timezone.now() - oldest.occurred_at + timedelta(seconds=1)


class TestRegisteringAChannel:
    def test_a_project_channel_is_used_by_name(
        self, monkeypatch, settings, parcel, bob, dispatched, django_capture_on_commit_callbacks
    ):
        from isik.django.apps.notifications.channels import Channel, Target, get_channel
        from isik.django.apps.notifications.models import InboxDelivery

        class Pager(Channel):
            name = "pager"

            def targets(self, notice, kind, users):
                return [Target(f"pager:{user.pk}", InboxDelivery, {"user_id": str(user.pk)}) for user in users]

            def send(self, delivery):
                return Outcome(succeeded=True)

        monkeypatch.setitem(registered, "pager", None)
        register_channel("pager", Pager)
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "CHANNELS": ("pager",)}
        notice = ship(parcel)

        fan_out(django_capture_on_commit_callbacks)

        assert deliveries(notice) == [("pager", f"pager:{bob.pk}")]
        assert isinstance(get_channel("pager"), Pager)

    def test_an_unregistered_one_is_refused(self):
        from isik.django.apps.notifications.channels import get_channel

        with pytest.raises(LookupError, match="^No channel is registered as 'fax'.$"):
            get_channel("fax")
