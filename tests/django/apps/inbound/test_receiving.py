"""A webhook received: verified, read, written down once, and answered - the handling left to a task."""

import json
import time

import pytest
from django.test import Client, TestCase

from isik.django.apps.inbound.choices import EventState
from isik.django.apps.inbound.models import InboundEvent
from tests.django.apps.inbound.conftest import post, signed


pytestmark = pytest.mark.django_db

INVOICE = {"id": "evt_1", "kind": "invoice_voided", "invoice": 7}


class TestWrittenDown:
    def test_once_and_answered(self, asked):
        answered = post("acme", INVOICE)

        event = InboundEvent.objects.get()
        assert answered.status_code == 200
        assert (event.source, event.event_id, event.event_type, event.payload) == (
            "acme",
            "evt_1",
            "invoice_voided",
            INVOICE,
        )
        assert (event.state, event.attempt_count, event.partition) == (EventState.PENDING, 0, None)
        assert asked == [(None, str(event.pk))]

    def test_a_resend_is_answered_and_dropped(self, asked):
        post("acme", INVOICE)
        again = post("acme", INVOICE)

        assert again.status_code == 200
        assert InboundEvent.objects.count() == 1
        assert len(asked) == 1

    def test_one_per_source_and_id(self, asked):
        post("acme", INVOICE)
        post("acme", INVOICE | {"id": "evt_2"})

        assert sorted(InboundEvent.objects.values_list("event_id", flat=True)) == ["evt_1", "evt_2"]

    def test_a_source_naming_no_id_writes_each_down(self, asked):
        post("pinger", {"at": 1}, header="X-Pinger-Signature")
        post("pinger", {"at": 1}, header="X-Pinger-Signature")

        assert list(InboundEvent.objects.values_list("event_id", "event_type")) == [(None, "ping"), (None, "ping")]

    def test_an_id_or_type_that_isnt_text_is_written_as_text(self, asked):
        post("acme", {"id": 41, "kind": 3})

        assert InboundEvent.objects.values_list("event_id", "event_type").get() == ("41", "3")


class TestRefused:
    def refusal(self, answered):
        return answered.status_code, answered.json()["code"]

    def test_a_source_not_declared(self):
        answered = post("nobody", INVOICE)

        assert self.refusal(answered) == (404, "unknown_source")
        assert answered.json()["detail"] == "No source called nobody is declared."

    def test_anything_but_a_post(self):
        assert Client().get("/in/acme/").status_code == 405

    def test_no_signature(self):
        answered = post("acme", INVOICE, signature=False)

        assert self.refusal(answered) == (401, "unverified")
        assert answered.json()["detail"] == "The request carries no X-Acme-Signature."
        assert not InboundEvent.objects.exists()

    @pytest.mark.parametrize("value", ["v1=abc", "t=abc,v1=abc", f"t={int(time.time())}", "t=1,t=2,v1=abc"])
    def test_a_signature_not_shaped_as_one(self, value):
        answered = post("acme", INVOICE, signature=value)

        assert self.refusal(answered) == (401, "unverified")
        assert answered.json()["detail"] == "X-Acme-Signature isn't t=<time>,v1=<signature>."

    @pytest.mark.parametrize("off", [-301, 301])
    def test_signed_too_long_ago_or_ahead(self, off):
        body = json.dumps(INVOICE).encode()

        answered = post("acme", INVOICE, signature=signed(body, stamp=int(time.time()) + off))

        assert answered.json()["detail"] == "X-Acme-Signature was signed too long ago, or ahead of now."

    def test_signed_within_its_tolerance(self, asked):
        body = json.dumps(INVOICE).encode()

        assert post("acme", INVOICE, signature=signed(body, stamp=int(time.time()) - 290)).status_code == 200

    def test_signed_by_another_secret(self):
        body = json.dumps(INVOICE).encode()

        answered = post("acme", INVOICE, signature=signed(body, secret="another"))

        assert answered.json()["detail"] == "X-Acme-Signature matches none of the source's secrets."

    def test_one_matching_signature_of_several_is_enough(self, asked):
        body = json.dumps(INVOICE).encode()
        other, ours = signed(body, secret="old"), signed(body)

        assert post("acme", INVOICE, signature=f"{other},{ours.split(',')[1]}").status_code == 200

    def test_a_body_that_isnt_json(self):
        answered = post("acme", None, raw=b"not json")

        assert self.refusal(answered) == (400, "unreadable")
        assert answered.json()["detail"].startswith("The body isn't one acme sends: ")

    def test_a_body_without_the_id(self):
        assert self.refusal(post("acme", {"kind": "invoice_voided"})) == (400, "unreadable")

    def test_a_body_with_an_empty_id(self):
        answered = post("acme", INVOICE | {"id": ""})

        assert self.refusal(answered) == (400, "unidentified")
        assert answered.json()["detail"] == "The body names no event id."


class TestPartitioned:
    def test_the_partition_the_request_names_is_entered_first_and_kept(self, settings, monkeypatch, asked):
        import dataclasses
        from contextlib import contextmanager

        from isik.django.apps.inbound import sources
        from isik.django.conf import isik_areas

        entered = []

        @contextmanager
        def in_partition(key):
            entered.append(key)
            yield

        isik_areas(settings).INBOUND = {
            **isik_areas(settings).INBOUND,
            "PARTITIONS": lambda: ["north"],
            "IN_PARTITION": in_partition,
        }
        monkeypatch.setitem(
            sources.sources,
            "acme",
            dataclasses.replace(sources.sources["acme"], partition=lambda request: request.headers["X-Tenant"]),
        )
        body = json.dumps(INVOICE).encode()

        with TestCase.captureOnCommitCallbacks(execute=True):
            answered = Client().post(
                "/in/acme/",
                body,
                content_type="application/json",
                headers={"X-Acme-Signature": signed(body), "X-Tenant": "north"},
            )

        assert (answered.status_code, entered) == (200, ["north"])
        event = InboundEvent.objects.get()
        assert event.partition == "north"
        assert asked == [("north", str(event.pk))]


class TestReadFromTheRequest:
    def test_the_id_and_type_may_be_read_from_its_headers(self, monkeypatch, asked):
        import dataclasses

        from isik.django.apps.inbound import sources

        monkeypatch.setitem(
            sources.sources,
            "acme",
            dataclasses.replace(
                sources.sources["acme"],
                event_id=lambda request, payload: request.headers["X-Event-Id"],
                event_type=lambda request, payload: request.headers["X-Event-Type"],
            ),
        )
        body = json.dumps({}).encode()

        Client().post(
            "/in/acme/",
            body,
            content_type="application/json",
            headers={"X-Acme-Signature": signed(body), "X-Event-Id": "evt_9", "X-Event-Type": "invoice_paid"},
        )

        assert InboundEvent.objects.values_list("event_id", "event_type").get() == ("evt_9", "invoice_paid")

    def test_a_refusal_is_logged_with_its_source_and_reason(self, caplog):
        post("acme", INVOICE, signature=False)

        (logged,) = [record for record in caplog.records if record.name == "isik.django.apps.inbound.views"]
        assert logged.getMessage() == "An inbound acme webhook was refused: The request carries no X-Acme-Signature."
