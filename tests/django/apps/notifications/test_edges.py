# ruff: noqa: F811 - the webhook fixtures are test_pipeline_details', imported by name and requested by it.
"""The edges each part has to get exactly right - the claim a worker finishes against, where a body's read
stops, the address that's unwrapped, what a refusal records - one test each."""

import functools
import socket
from contextlib import contextmanager
from datetime import timedelta

import httpcore
import pgtrigger
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from isik.django.apps.notifications import checks, kinds, pipeline
from isik.django.apps.notifications.audience.swapping import get_endpoint_model
from isik.django.apps.notifications.audiences import Subscribers
from isik.django.apps.notifications.channels import guard
from isik.django.apps.notifications.channels import webhook as webhook_module
from isik.django.apps.notifications.kinds import KindName, declare_kind, notifies
from isik.django.apps.notifications.models import (
    Delivery,
    DeliveryAttempt,
    DeliveryState,
    FanOutShard,
    Notice,
    Policy,
    ShardState,
)
from isik.django.apps.notifications.partitions import Column, Expression, Follow
from isik.django.apps.notifications.pipeline import retry_delivery
from tests.django.apps.notifications.test_pipeline import delivery_on, fan_out, ship
from tests.django.apps.notifications.test_pipeline_details import hooked, receiver, shipped  # noqa: F401
from tests.testapp.models import EmailUser, Parcel, Team


@pytest.fixture(autouse=True)
def _no_jitter(monkeypatch):
    monkeypatch.setattr(pipeline.random, "uniform", lambda low, high: 0)


class TestWhatIsntAColumn:
    def test_a_many_to_many_field_isnt_a_column_or_a_key_to_follow(self):
        kind = kinds.get_kind("parcel_shipped")

        assert Column("members").check(Team, kind) == [
            "parcel_shipped reads its partition from members, which testapp.Team has no column for."
        ]
        assert Follow("members", "username").check(Team, kind) == [
            "parcel_shipped follows members to its partition, which isn't a key of testapp.Team."
        ]

    def test_a_reverse_relation_isnt_either(self):
        kind = kinds.get_kind("parcel_shipped")

        assert Column("reports").check(EmailUser, kind) == [
            "parcel_shipped reads its partition from reports, which testapp.EmailUser has no column for."
        ]


def test_an_expression_reads_as_its_builder_without_a_name():
    def build(model, quote):
        return quote

    builder = functools.partial(build, quote="'x'")

    assert repr(Expression(builder)) == f"Expression({builder!r})"


def test_a_kind_partitioned_by_a_resolver_reads_its_partition_off_the_model():
    [trigger] = [trigger for trigger in Parcel._meta.triggers if trigger.name == "notify_parcel_routed"]

    assert 'SELECT "username" FROM "testapp_emailuser"' in trigger.get_func(Parcel)


@pytest.mark.django_db
def test_the_routed_kind_is_installed_as_compiled():
    [trigger] = [trigger for trigger in Parcel._meta.triggers if trigger.name == "notify_parcel_routed"]

    assert trigger.get_installation_status(Parcel)[0] == "INSTALLED"


class TestDeclaringDetails:
    def test_a_bare_reason_names_where_it_was_given(self):
        from django.core.exceptions import ImproperlyConfigured

        with pytest.raises(
            ImproperlyConfigured,
            match=r"^k\.sensitive takes a SensitiveSnapshotField\(reason=\.\.\.\) for secret, not a bare string\.$",
        ):
            notifies("k", audience=Subscribers, policy=Policy.MANDATORY, on=pgtrigger.Update, sensitive={"secret": "x"})

    def test_an_audience_alone_declares_behavior(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])

        declare_kind("half", audience=Subscribers)

        [kind] = kinds.declared
        assert (kind.name, kind.policy) == ("half", None)

    def test_raising_by_a_kind_name(self, db):
        from tests.testapp.models import PARCEL_REMINDER

        assert isinstance(PARCEL_REMINDER, KindName)
        assert kinds.raise_notice(PARCEL_REMINDER).kind_id == "parcel_reminder"


class TestCheckDetails:
    def test_a_resolvers_problems_are_added_to_the_rest(self, declaring):
        import dataclasses

        declaring(
            dataclasses.replace(
                kinds.get_kind("parcel_shipped"), snapshot=("status", "colour", "recipient"), partition=Column("x")
            )
        )

        assert checks.kind_problems() == [
            "parcel_shipped's snapshot names colour, which testapp.Parcel has no column for.",
            "parcel_shipped reads its partition from x, which testapp.Parcel has no column for.",
        ]

    def test_a_kind_writing_variants_its_declaration_names_none_of(self, monkeypatch):
        others = [name for name in kinds.vocabulary if name != "parcel_flagged"]
        monkeypatch.setattr(kinds, "vocabulary", [*others, KindName("parcel_flagged")])

        assert checks._vocabulary_problems() == ["parcel_flagged writes the variants damaged, lost, and declares none."]

    def test_every_email_kind_is_read_for_its_templates(self, declaring):
        import dataclasses

        shipped_kind = kinds.get_kind("parcel_shipped")
        declaring(
            dataclasses.replace(shipped_kind, name="by_inbox", channels=("inbox",)),
            dataclasses.replace(shipped_kind, name="by_email"),
        )

        assert checks._template_problems()[0].startswith("by_email is sent by email")

    def test_the_secret_snapshot_warning_in_full(self, declaring):
        import dataclasses

        declaring(
            dataclasses.replace(kinds.get_kind("parcel_shipped"), snapshot=("status", "recipient", "tracking_secret"))
        )

        [warning] = checks.notifications_can_work(app_configs=None)
        assert (warning.id, warning.hint) == ("isik.W005", "See isik.django.apps.notifications.")
        assert warning.msg.startswith("parcel_shipped's snapshot carries tracking_secret, which looks secret")


@pytest.mark.django_db
class TestASetAsideNotice:
    def test_keeps_its_partition_and_waits_its_first_backoff(
        self, monkeypatch, alice, dispatched, django_capture_on_commit_callbacks
    ):
        from tests.django.apps.notifications.test_pipeline import redeclared
        from tests.django.apps.notifications.test_resilience import InPartition

        class Down(InPartition):
            def recipients(self, notice):
                raise RuntimeError("down")

        Parcel.objects.create(sender=alice, status="routed")
        notice = Notice.objects.get(kind_id="parcel_routed")

        with redeclared(monkeypatch, "parcel_routed", audience=Down(notice.subject_partition)):
            fan_out(django_capture_on_commit_callbacks)

        shard = FanOutShard.objects.get(notice=notice)
        assert shard.partition == alice.username
        assert timedelta(seconds=59) < shard.next_attempt_at - timezone.now() <= timedelta(seconds=60)


@pytest.mark.django_db
class TestFinishingOnlyYourOwnClaim:
    def test_a_shard_the_reaper_took_back_meanwhile_isnt_finished(self, monkeypatch, parcel):
        shard = FanOutShard.objects.create(notice=ship(parcel), partition=None)

        def reaped(shard, kind):
            FanOutShard.objects.filter(pk=shard.pk).update(state=ShardState.PENDING)
            return [], []

        monkeypatch.setattr(pipeline, "_shard_work", reaped)

        assert pipeline.run_shard(shard.pk) is None
        assert FanOutShard.objects.get(pk=shard.pk).state != ShardState.FINISHED

    def test_another_shard_claimed_at_the_same_moment_is_left_alone(self, monkeypatch, parcel):
        notice = ship(parcel)
        shard = FanOutShard.objects.create(notice=notice, partition=None)
        other = FanOutShard.objects.create(notice=notice, partition="other", state=ShardState.CLAIMED)

        def twin(shard, kind):
            FanOutShard.objects.filter(pk=other.pk).update(claimed_at=shard.claimed_at)
            return [], []

        monkeypatch.setattr(pipeline, "_shard_work", twin)

        assert pipeline.run_shard(shard.pk).state == ShardState.FINISHED
        assert FanOutShard.objects.get(pk=other.pk).state == ShardState.CLAIMED

    def test_a_shard_out_of_attempts_says_why(self, monkeypatch, parcel, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "MAX_ATTEMPTS": 1}
        shard = FanOutShard.objects.create(notice=ship(parcel), partition=None)
        monkeypatch.setattr(pipeline, "_shard_work", lambda shard, kind: (_ for _ in ()).throw(RuntimeError("down")))

        ran = pipeline.run_shard(shard.pk)

        assert (ran.state, ran.last_error) == (ShardState.ABANDONED, "RuntimeError: down")

    def test_a_delivery_the_reaper_took_back_meanwhile_isnt_finished(self, monkeypatch, shipped):
        inbox = delivery_on(shipped, "inbox")

        def reaped(self, delivery):
            Delivery.objects.filter(pk=delivery.pk).update(state=DeliveryState.PENDING)
            return pipeline.Outcome(succeeded=True)

        monkeypatch.setattr("isik.django.apps.notifications.channels.inbox.InboxChannel.send", reaped)

        assert pipeline.send(inbox.pk) is None
        assert Delivery.objects.get(pk=inbox.pk).state == DeliveryState.PENDING

    def test_another_delivery_claimed_at_the_same_moment_is_left_alone(self, monkeypatch, shipped):
        inbox, email = delivery_on(shipped, "inbox"), delivery_on(shipped, "email")

        def twin(self, delivery):
            Delivery.objects.filter(pk=email.pk).update(
                state=DeliveryState.SENDING, claimed_at=Delivery.objects.get(pk=delivery.pk).claimed_at
            )
            return pipeline.Outcome(succeeded=True)

        monkeypatch.setattr("isik.django.apps.notifications.channels.inbox.InboxChannel.send", twin)

        assert pipeline.send(inbox.pk).state == DeliveryState.SENT
        assert Delivery.objects.get(pk=email.pk).state == DeliveryState.SENDING


@pytest.mark.django_db
class TestRetryDetails:
    @pytest.fixture
    def abandoned(self, hooked, receiver):
        receiver.status = 404
        delivery = pipeline.send(hooked[0].pk)
        Delivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now() + timedelta(days=1))
        return delivery

    def test_it_is_due_now_whenever_it_was_due_before(self, abandoned):
        before = timezone.now()

        retried = retry_delivery(abandoned)

        assert before <= retried.next_attempt_at <= timezone.now()

    def test_it_holds_its_notice_while_it_decides(self, abandoned):
        with CaptureQueriesContext(connection) as queries:
            retry_delivery(abandoned)

        held = [query["sql"] for query in queries if "isik_notice" in query["sql"] and "FOR UPDATE" in query["sql"]]
        assert len(held) == 1
        assert str(abandoned.notice_id) in held[0]

    def test_a_parked_endpoint_in_a_partition_says_so(self, abandoned, settings):
        @contextmanager
        def enter(key):
            yield

        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "PARTITIONS": lambda: ["north"],
            "IN_PARTITION": enter,
        }
        Delivery.objects.filter(pk=abandoned.pk).update(partition="north")
        get_endpoint_model().objects.update(parked_at=timezone.now())

        with pytest.raises(pipeline.RetryRefused, match="^The endpoint is parked - unpark it first.$"):
            retry_delivery(abandoned)


@pytest.mark.django_db
class TestReapingDetails:
    def test_a_delivery_claimed_lately_isnt_taken_back(self, shipped, dispatched):
        inbox = delivery_on(shipped, "inbox")
        Delivery.objects.filter(pk=inbox.pk).update(state=DeliveryState.SENDING, claimed_at=timezone.now())

        pipeline.reap_stranded()

        assert Delivery.objects.get(pk=inbox.pk).state == DeliveryState.SENDING

    def test_whats_done_however_overdue_isnt_asked_for(self, shipped, dispatched):
        long_ago = timezone.now() - timedelta(hours=1)
        Delivery.objects.filter(notice=shipped).update(state=DeliveryState.SENT, next_attempt_at=long_ago)
        FanOutShard.objects.create(notice=shipped, partition="a", state=ShardState.FINISHED, next_attempt_at=long_ago)

        assert pipeline.reap_stranded() == {"deliveries": 0, "shards": 0}


@pytest.mark.django_db
class TestDeliveriesListedInFewQueries:
    def test_the_same_queries_for_one_delivery_or_many(
        self, monkeypatch, settings, alice, bob, dispatched, django_capture_on_commit_callbacks
    ):
        from rest_framework.test import APIClient

        settings.ROOT_URLCONF = "tests.django.apps.notifications.api.urls"
        monkeypatch.setitem(connection.settings_dict, "ATOMIC_REQUESTS", True)

        from isik.django.apps.notifications.audience.helpers import subscribe
        from isik.django.apps.notifications.audience.swapping import get_endpoint_kind_model
        from tests.django.apps.notifications.test_pipeline import flag

        endpoint = get_endpoint_model().objects.create(url="https://alice.example/hook", secret="s")
        get_endpoint_kind_model().objects.create(endpoint=endpoint, kind_id="parcel_flagged")
        subscribe(alice, "parcel_flagged")
        client = APIClient()
        client.force_authenticate(alice)

        def queries_listing():
            with CaptureQueriesContext(connection) as queries:
                client.get("/deliveries/")
            return len(queries)

        flag(alice)
        fan_out(django_capture_on_commit_callbacks)
        one = queries_listing()
        flag(alice)
        flag(alice)
        fan_out(django_capture_on_commit_callbacks)

        assert queries_listing() == one


@pytest.mark.django_db
class TestReadingABody:
    def test_its_chunks_are_put_together(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}
        receiver.chunks = [b"ab", b"cd"]

        pipeline.send(hooked[0].pk)

        assert DeliveryAttempt.objects.get().response["body"] == "abcd"

    def test_a_full_body_is_never_timed(self, monkeypatch, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all", "MAX_RESPONSE_BYTES": 4}
        receiver.chunks = [b"abcd", b"efgh"]
        clock = iter([0.0, 0.0, 100.0])
        monkeypatch.setattr(webhook_module.time, "monotonic", lambda: next(clock, 100.0))

        assert pipeline.send(hooked[0].pk).state == DeliveryState.SENT
        assert DeliveryAttempt.objects.get().response["body"] == "abcd"

    def test_the_deadline_itself_is_still_in_time(self, monkeypatch, hooked, receiver):
        receiver.chunks = [b"a", b"b"]
        clock = iter([0.0, 0.0, 10.0])
        monkeypatch.setattr(webhook_module.time, "monotonic", lambda: next(clock, 10.0))

        assert pipeline.send(hooked[0].pk).state == DeliveryState.SENT

    def test_bytes_that_arent_utf8_are_replaced(self, hooked, receiver, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}
        receiver.chunks = [b"ok\xff"]

        pipeline.send(hooked[0].pk)

        assert DeliveryAttempt.objects.get().response["body"] == "ok�"

    def test_it_is_posted(self, hooked, receiver):
        pipeline.send(hooked[0].pk)

        assert receiver.requests[0].method == "POST"

    def test_a_refusal_records_what_would_have_been_sent(self, hooked):
        get_endpoint_model().objects.filter(pk=hooked[1].pk).update(url="http://127.0.0.1:9/in")

        pipeline.send(hooked[0].pk)

        assert DeliveryAttempt.objects.get().request["url"] == "http://127.0.0.1:9/in"


class TestTheGuardsDetails:
    def test_a_mapped_address_is_judged_as_the_ipv4_one_it_is(self, settings):
        settings.ISIK_NOTIFICATIONS = {"ALLOW_PRIVATE_TARGETS": ["10.0.0.0/8"]}

        guard.check("::ffff:10.0.0.1")

    def test_the_connection_is_made_as_asked_to_the_address_checked(self, monkeypatch):
        resolved, connected = [], []
        monkeypatch.setattr(guard, "resolve", lambda host, port: resolved.append((host, port)) or "93.184.215.14")
        monkeypatch.setattr(
            httpcore.SyncBackend,
            "connect_tcp",
            lambda self, *args: connected.append(args),
        )

        guard.GuardedBackend().connect_tcp("hooks.example", 443, 3, "10.9.9.9", [("option",)])

        assert resolved == [("hooks.example", 443)]
        assert connected == [("93.184.215.14", 443, 3, "10.9.9.9", [("option",)])]

    def test_resolving_takes_the_first_address(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda host, port, type: [(2, type, 6, "", ("1.1.1.1", port)), (2, type, 6, "", ("2.2.2.2", port))],
        )

        assert guard.resolve("hooks.example", 443) == "1.1.1.1"
