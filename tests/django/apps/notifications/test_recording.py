"""record_email(): an email its caller sends itself, recorded in the ledger - and never stopped by it."""

import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.utils import timezone

from isik.django.apps.notifications import checks, kinds, pipeline
from isik.django.apps.notifications.audiences import Subscribers
from isik.django.apps.notifications.kinds import declare_kind, raise_notice
from isik.django.apps.notifications.management.commands.notices import describe
from isik.django.apps.notifications.models import (
    Delivery,
    DeliveryAttempt,
    DeliveryState,
    EmailDelivery,
    Notice,
    NoticeKindPolicy,
    Policy,
    Source,
)
from isik.django.apps.notifications.pipeline import RetryRefused, record_email, retry_delivery
from tests.testapp.models import PARCEL_RECEIPT


pytestmark = pytest.mark.django_db


class Sender:
    """A send() that records it was called, and fails with `error` when given one."""

    def __init__(self, error=None):
        self.calls, self.error = 0, error

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error


def test_a_sent_email_is_recorded_as_sent(alice, parcel, dispatched, settings):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}
    send = Sender()

    delivery = record_email(
        PARCEL_RECEIPT,
        address="Alice@Example.com",
        send=send,
        user=alice,
        variant="signed",
        about=parcel,
        partition="p1",
    )

    assert send.calls == 1
    notice = delivery.notice
    assert (notice.kind.name, notice.variant, notice.subject_label, notice.subject_id, notice.subject_partition) == (
        "parcel_receipt",
        "signed",
        "testapp.Parcel",
        str(parcel.pk),
        "p1",
    )
    assert notice.fanned_out_at is not None
    assert (delivery.channel, delivery.partition, delivery.target_key, delivery.state, delivery.attempt_count) == (
        "email",
        "p1",
        "email:alice@example.com",
        DeliveryState.SENT,
        1,
    )
    assert delivery.sent_at is not None
    assert (delivery.email.address, delivery.email.user_id) == ("Alice@Example.com", str(alice.pk))
    attempt = DeliveryAttempt.objects.get(delivery=delivery)
    assert (attempt.attempt_no, attempt.succeeded, attempt.request, attempt.triggered_by, attempt.error) == (
        1,
        True,
        {"to": "Alice@Example.com"},
        "record_email",
        "",
    )
    assert attempt.duration_ms >= 0
    assert attempt.started_at <= timezone.now()


def test_the_fan_out_never_takes_a_record(dispatched):
    record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender())

    assert pipeline.fan_out() == 0
    assert dispatched == []


def test_with_no_user_or_row(dispatched):
    delivery = record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender())

    assert (delivery.notice.subject_label, delivery.notice.subject_id, delivery.notice.snapshot) == (None, None, {})
    assert (delivery.email.user_id, delivery.partition) == (None, None)


def test_a_snapshot_is_kept(dispatched):
    delivery = record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(), snapshot={"reason": "signup"})

    assert delivery.notice.snapshot == {"reason": "signup"}


def test_a_failed_send_is_recorded_and_raised_again(alice, parcel, dispatched):
    send = Sender(ConnectionRefusedError("smtp is down"))

    with pytest.raises(ConnectionRefusedError, match="smtp is down"):
        record_email(
            PARCEL_RECEIPT,
            address="a@example.com",
            send=send,
            user=alice,
            variant="signed",
            about=parcel,
            snapshot={"reason": "signup"},
            partition="p1",
        )

    delivery = Delivery.objects.get()
    assert (delivery.state, delivery.sent_at, delivery.attempt_count, delivery.partition) == (
        DeliveryState.ABANDONED,
        None,
        1,
        "p1",
    )
    notice = delivery.notice
    assert (notice.variant, notice.subject_id, notice.snapshot) == ("signed", str(parcel.pk), {"reason": "signup"})
    assert delivery.email.user_id == str(alice.pk)
    attempt = DeliveryAttempt.objects.get(delivery=delivery)
    assert (attempt.succeeded, attempt.error, attempt.request) == (
        False,
        "ConnectionRefusedError: smtp is down",
        {"to": "a@example.com"},
    )


def test_the_attempt_takes_as_long_as_the_send(dispatched, monkeypatch, settings):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}
    ticks = iter([10.0, 12.5])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(ticks, 11.0))

    record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender())

    assert DeliveryAttempt.objects.get().duration_ms == 2500


def test_a_record_is_never_retried(dispatched):
    with pytest.raises(ConnectionRefusedError):
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(ConnectionRefusedError()))

    with pytest.raises(RetryRefused, match="^This email was sent by its caller and only recorded here"):
        retry_delivery(Delivery.objects.get())


@pytest.mark.parametrize(
    "broken",
    [
        {"kind": "parcel_shipped"},
        {"kind": "nothing"},
        {"variant": "torn"},
    ],
)
def test_nothing_wrong_with_the_record_stops_the_email(dispatched, caplog, broken):
    send = Sender()
    call = {"kind": PARCEL_RECEIPT, "address": "a@example.com", "send": send, **broken}

    with caplog.at_level(logging.ERROR, logger="isik.django.apps.notifications.pipeline"):
        assert record_email(**call) is None

    assert send.calls == 1
    assert not Notice.objects.exists()
    assert (
        caplog.records[0].getMessage()
        == f"An email of {call['kind']} to a@example.com was sent and could not be recorded."
    )


def test_a_broken_ledger_never_stops_the_email(dispatched, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise DatabaseError("the ledger is gone")

    monkeypatch.setattr(pipeline, "write_notice", broken)
    send = Sender()

    with caplog.at_level(logging.ERROR, logger="isik.django.apps.notifications.pipeline"):
        assert record_email(PARCEL_RECEIPT, address="a@example.com", send=send) is None

    assert send.calls == 1


def test_a_broken_ledger_raises_only_what_send_raised(dispatched, monkeypatch):
    monkeypatch.setattr(pipeline, "_retired", lambda name: 1 / 0)

    with pytest.raises(ConnectionRefusedError):
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(ConnectionRefusedError()))


def test_a_retired_kind_is_sent_and_not_recorded(dispatched, caplog):
    NoticeKindPolicy.objects.filter(name="parcel_receipt").update(retired_at=timezone.now())
    send = Sender()

    with caplog.at_level(logging.WARNING, logger="isik.django.apps.notifications.pipeline"):
        assert record_email(PARCEL_RECEIPT, address="a@example.com", send=send) is None

    assert send.calls == 1
    assert not Notice.objects.exists()
    assert caplog.records[0].getMessage() == "An email of parcel_receipt, which is retired, was sent and not recorded."


def test_failing_attempts_alone_are_recorded_by_default(dispatched):
    record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender())

    assert EmailDelivery.objects.count() == 1
    assert not DeliveryAttempt.objects.exists()


class TestARecordedKind:
    def test_its_declared_mandatory_on_email_alone(self):
        kind = kinds.get_kind("parcel_receipt")

        assert (kind.source, kind.policy, kind.channels, kind.audience, kind.variants) == (
            Source.RECORDED,
            Policy.MANDATORY,
            ("email",),
            None,
            ("signed",),
        )
        assert NoticeKindPolicy.objects.get(name="parcel_receipt").source == Source.RECORDED

    @pytest.mark.parametrize("given", [{"audience": "x"}, {"policy": Policy.DEFAULT_ON}, {"channels": ("email",)}])
    def test_it_takes_nothing_isik_would_send_it_by(self, given):
        with pytest.raises(ImproperlyConfigured, match="^receipt is recorded - mandatory, on email alone"):
            declare_kind("receipt", source=Source.RECORDED, **given)

    def test_its_declared_so_wherever_its_declared(self, monkeypatch):
        monkeypatch.setattr(kinds, "declared", [])
        monkeypatch.setattr(kinds, "vocabulary", [])

        declare_kind("receipt", variants=("signed",), source=Source.RECORDED)

        assert kinds.declared == [
            kinds.Kind(
                name="receipt",
                audience=None,
                policy=Policy.MANDATORY,
                source=Source.RECORDED,
                variants=("signed",),
                channels=("email",),
            )
        ]

    def test_its_checks_pass_over_it_to_the_next(self, declaring):
        declaring(
            kinds.get_kind("parcel_receipt"),
            kinds.Kind(name="odd", audience=Subscribers(), policy="sometimes", source=Source.APPLICATION),
        )

        assert checks.kind_problems() == ["odd's policy is a Policy, not 'sometimes'."]

    def test_its_listed_with_no_audience(self):
        assert describe(kinds.get_kind("parcel_receipt"))["audience"] is None

    def test_it_isnt_raised(self):
        with pytest.raises(
            ImproperlyConfigured, match="^parcel_receipt is not a declared kind whose source is application"
        ):
            raise_notice(PARCEL_RECEIPT)


class TestOneEmailAcrossItsTries:
    def test_a_retry_adds_its_attempt_to_the_first_delivery(self, alice, dispatched, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECORD_ATTEMPTS": "all"}

        for error in (ConnectionRefusedError("down"), TimeoutError("slow")):
            with pytest.raises(type(error)):
                record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(error), user=alice, key="task-1")
        delivery = record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(), key="task-1")

        assert Notice.objects.count() == 1
        assert (delivery.state, delivery.attempt_count, delivery.email.record_key) == (DeliveryState.SENT, 3, "task-1")
        assert delivery.sent_at is not None
        assert list(
            DeliveryAttempt.objects.order_by("attempt_no").values_list(
                "attempt_no", "succeeded", "error", "triggered_by"
            )
        ) == [
            (1, False, "ConnectionRefusedError: down", "record_email"),
            (2, False, "TimeoutError: slow", "record_email"),
            (3, True, "", "record_email"),
        ]

    def test_its_abandoned_between_a_failed_try_and_the_next(self, dispatched):
        with pytest.raises(ConnectionRefusedError):
            record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(ConnectionRefusedError()), key="t")
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(), key="t")

        with pytest.raises(ConnectionRefusedError):
            record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(ConnectionRefusedError()), key="t")

        delivery = Delivery.objects.get()
        assert (delivery.state, delivery.sent_at, delivery.attempt_count) == (DeliveryState.ABANDONED, None, 3)

    def test_other_keys_are_other_emails(self, dispatched):
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(), key="one")
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(), key="two")
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender())
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender())

        assert Delivery.objects.count() == 4
        assert sorted(EmailDelivery.objects.values_list("record_key", flat=True), key=str) == [None, None, "one", "two"]

    def test_a_retired_kind_still_records_a_retry_of_an_email_recorded_before(self, dispatched):
        record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(), key="t")
        NoticeKindPolicy.objects.filter(name="parcel_receipt").update(retired_at=timezone.now())

        assert record_email(PARCEL_RECEIPT, address="a@example.com", send=Sender(), key="t").attempt_count == 2
