"""Declaring sources and handlers, and isik.E028 - an inbound setting or declaration that can't work."""

from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test.utils import isolate_apps

from isik.django.apps.inbound import checks, sources
from isik.django.apps.inbound.checks import inbound_can_work, setting_problems, source_problems
from isik.django.apps.inbound.choices import PayloadKept
from isik.django.apps.inbound.sources import (
    IsikSignature,
    SharedSecret,
    Unidentified,
    declare_source,
    handler_for,
    handles,
    import_sources_module,
    json_payload,
)
from isik.django.conf import isik_areas


AREA = "ISIK['DJANGO']['INBOUND']"
VERIFIER = IsikSignature(header="X-Signature", secrets=lambda request: ["s"], tolerance=timedelta(minutes=5))


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setattr(sources, "sources", {})
    monkeypatch.setattr(sources, "handlers", {})
    monkeypatch.setattr(checks, "sources", sources.sources)
    monkeypatch.setattr(checks, "handlers", sources.handlers)
    return sources


def a_source(name="billing", **given):
    return declare_source(
        name,
        **{
            "verifier": VERIFIER,
            "event_id": lambda request, payload: payload["id"],
            "event_type": lambda request, payload: payload["type"],
            **given,
        },
    )


class TestDeclaring:
    def test_a_source(self, declared):
        source = a_source()

        assert declared.sources == {"billing": source}
        assert (source.partition, source.parse, source.payload_kept) == (None, json_payload, PayloadKept.WITH_THE_EVENT)
        assert source.identifies_events is True

    def test_a_source_naming_no_id(self, declared):
        source = a_source(event_id=Unidentified(reason="the sender posts heartbeats only, each the same as the last"))

        assert source.identifies_events is False

    def test_a_name_twice(self, declared):
        a_source()

        with pytest.raises(ImproperlyConfigured) as refused:
            a_source()

        assert str(refused.value) == "A source called billing is declared already."

    def test_what_it_was_given(self, declared):
        def parse(request):
            return {}

        source = a_source("shipping", parse=parse, payload_kept=PayloadKept.UNTIL_HANDLED)

        assert (source.name, source.parse, source.payload_kept) == ("shipping", parse, PayloadKept.UNTIL_HANDLED)

    def test_handlers_by_type_and_for_every_type(self, declared):
        @handles("billing", "paid", "voided")
        def invoice(event):
            pass

        @handles("billing")
        def anything(event):
            pass

        assert (handler_for("billing", "paid"), handler_for("billing", "voided")) == (invoice, invoice)
        assert handler_for("billing", "refunded") is anything
        assert handler_for("shipping", "paid") is None

    def test_a_handler_twice(self, declared):
        handles("billing", "paid")(print)

        with pytest.raises(ImproperlyConfigured) as refused:
            handles("billing", "paid")(print)
        assert str(refused.value) == "billing's paid events have a handler already."
        handles("billing")(print)
        with pytest.raises(ImproperlyConfigured, match="billing's every events have a handler already."):
            handles("billing")(print)

    def test_the_sources_module_is_imported_where_its_named(self, settings, monkeypatch):
        imported = []
        monkeypatch.setattr(sources, "import_module", imported.append)
        isik_areas(settings).INBOUND = {"SOURCES": "billing.inbound"}
        import_sources_module()
        isik_areas(settings).INBOUND = {}
        import_sources_module()

        assert imported == ["billing.inbound"]


class TestTheSettings:
    def problems(self, settings, **given):
        isik_areas(settings).INBOUND = {"BACKOFF": (60,), "STRANDED_AFTER": timedelta(minutes=10), **given}
        return setting_problems()

    def test_the_test_projects_are_valid(self):
        assert setting_problems() == []
        assert source_problems() == []

    def test_valid_edges(self, settings):
        assert (
            self.problems(
                settings,
                MAX_ATTEMPTS=1,
                BATCH=1,
                RETENTION=timedelta(seconds=1),
                PARTITIONS=list,
                IN_PARTITION="contextlib.nullcontext",
                EVENT_MODEL="isik_inbound.InboundEvent",
            )
            == []
        )

    def test_keys_it_doesnt_know(self, settings):
        assert self.problems(settings, COLOR="red", SIZE=1) == [f"{AREA} has keys it doesn't know: COLOR, SIZE."]

    @pytest.mark.parametrize("backoff", [None, (), [], (60, 0), (60, "300"), (True,), 60])
    def test_a_backoff_that_isnt_one(self, settings, backoff):
        assert self.problems(settings, BACKOFF=backoff) == [
            f"{AREA}['BACKOFF'] is the seconds to wait after each failed attempt, such as (60, 300, 1800)."
        ]

    def test_a_list_backoff(self, settings):
        assert self.problems(settings, BACKOFF=[60, 300]) == []

    @pytest.mark.parametrize("stranded", [None, timedelta(0), 600])
    def test_a_stranded_after_that_isnt_a_period(self, settings, stranded):
        assert self.problems(settings, STRANDED_AFTER=stranded) == [
            f"{AREA}['STRANDED_AFTER'] is how long a worker may hold an event before it's taken back, a timedelta."
        ]

    @pytest.mark.parametrize("name", ["MAX_ATTEMPTS", "BATCH"])
    @pytest.mark.parametrize("value", [0, -1, True, "5"])
    def test_a_count_that_isnt_one(self, settings, name, value):
        assert self.problems(settings, **{name: value}) == [f"{AREA}['{name}'] is at least 1, or None."]

    @pytest.mark.parametrize("retention", [timedelta(0), 30])
    def test_a_retention_that_isnt_a_period(self, settings, retention):
        assert self.problems(settings, RETENTION=retention) == [
            f"{AREA}['RETENTION'] is a timedelta longer than none, or None."
        ]

    @pytest.mark.parametrize("given", [{"PARTITIONS": list}, {"IN_PARTITION": "contextlib.nullcontext"}])
    def test_one_of_the_partition_hooks(self, settings, given):
        assert self.problems(settings, **given) == [
            f"{AREA} sets one of PARTITIONS and IN_PARTITION - set both, or neither."
        ]


class TestTheSources:
    def test_a_source_that_cant_work(self, declared):
        a_source(verifier=object(), event_id="id", event_type=None)

        assert source_problems() == [
            "billing's verifier has no verify(request).",
            "billing's event_id is f(request, payload), or Unidentified(reason=...).",
            "billing's event_type is f(request, payload).",
        ]

    def test_a_source_naming_no_id_needs_no_function_for_it(self, declared):
        a_source(event_id=Unidentified(reason="the sender posts heartbeats only, each the same as the last"))

        assert source_problems() == []

    def test_a_partitioned_project_and_a_source_naming_no_partition(self, declared, settings):
        isik_areas(settings).INBOUND = {"PARTITIONS": list, "IN_PARTITION": "contextlib.nullcontext"}
        a_source()
        a_source("shipping", partition=lambda request: "north")

        assert source_problems() == [
            f"billing names no partition, and {AREA} sets partitions - say which one a request is for with "
            "partition=, read before anything is written."
        ]

    def test_a_source_naming_a_partition_with_none_to_enter(self, declared):
        a_source(partition=lambda request: "north")

        assert source_problems() == [
            f"billing names a partition, and {AREA} sets no PARTITIONS and IN_PARTITION to enter it by."
        ]

    def test_a_handler_for_a_source_not_declared(self, declared):
        a_source()
        handles("billing", "paid")(print)
        handles("shipping", "sent")(print)
        handles("shipping")(print)

        assert source_problems() == ["A handler takes shipping's events, and no source is called that."]

    def test_each_is_an_error(self, declared, settings):
        isik_areas(settings).INBOUND = {"COLOR": "red", "BACKOFF": (60,), "STRANDED_AFTER": timedelta(minutes=1)}
        handles("shipping", "sent")(print)

        found = inbound_can_work(None)

        assert [(error.id, error.hint) for error in found] == [("isik.E028", "See isik.django.apps.inbound.")] * 2
        assert [error.msg for error in found] == [
            f"{AREA} has keys it doesn't know: COLOR.",
            "A handler takes shipping's events, and no source is called that.",
        ]


class TestTheEventModel:
    def test_isiks_unless_the_project_names_its_own(self, settings):
        from isik.django.apps.inbound import swapping
        from isik.django.apps.inbound.models import InboundEvent

        assert swapping.get_inbound_event_model() is InboundEvent
        settings.ISIK_INBOUND_EVENT_MODEL = "testapp.Widget"
        swapping.set_defaults()

        assert settings.ISIK_INBOUND_EVENT_MODEL == "testapp.Widget"


class TestAProjectsOwnEventModel:
    @isolate_apps("tests.testapp")
    def test_gets_the_rules_whatever_its_meta(self):
        from django.db import models

        from isik.django.apps.inbound.bases import AbstractInboundEvent
        from isik.django.apps.inbound.choices import EventState

        class OwnEvent(AbstractInboundEvent):
            class Meta:
                app_label = "testapp"
                db_table = "own_event"
                constraints = [models.CheckConstraint(condition=models.Q(attempt_count__gte=0), name="own_counted")]

        (own, once) = OwnEvent._meta.constraints
        assert own.name == "own_counted"
        assert (once.name, once.fields, once.condition) == (
            "own_event_once",
            ("source", "event_id"),
            models.Q(event_id__isnull=False),
        )
        assert (once.violation_error_code, str(once.violation_error_message)) == (
            "event_received",
            "This event was received already.",
        )
        assert [(index.name, index.fields, index.condition) for index in OwnEvent._meta.indexes] == [
            ("own_event_due", ["next_attempt_at"], models.Q(state=EventState.PENDING)),
            ("own_event_claimed", ["claimed_at"], models.Q(state=EventState.PROCESSING)),
            ("own_event_received", ["received_at"], None),
        ]
        assert OwnEvent._meta.original_attrs["constraints"] is OwnEvent._meta.constraints
        assert OwnEvent._meta.original_attrs["indexes"] is OwnEvent._meta.indexes

    @isolate_apps("tests.testapp")
    def test_its_indexes_named_within_what_an_index_takes(self):
        from django.db import models

        from isik.django.apps.common.db.constraints import fitted_name
        from isik.django.apps.inbound.bases import AbstractInboundEvent

        table = "a_project_whose_inbound_events_table_has_a_long_name"

        class LongNamed(AbstractInboundEvent):
            class Meta:
                app_label = "testapp"
                db_table = table

        assert [index.name for index in LongNamed._meta.indexes] == [
            fitted_name(table, rule, limit=models.Index.max_name_length) for rule in ("due", "claimed", "received")
        ]
        assert all(len(index.name) <= models.Index.max_name_length for index in LongNamed._meta.indexes)

    @isolate_apps("tests.testapp")
    def test_a_proxy_and_a_model_of_another_kind_are_left_be(self):
        from django.db import models

        from isik.django.apps.inbound.models import InboundEvent

        class Elsewhere(models.Model):
            class Meta:
                app_label = "testapp"

        class Seen(InboundEvent):
            class Meta:
                app_label = "testapp"
                proxy = True

        assert (Elsewhere._meta.constraints, Elsewhere._meta.indexes) == ([], [])
        assert (Seen._meta.constraints, Seen._meta.indexes) == ([], [])


class TestTheConf:
    def test_an_area_that_isnt_a_dict_is_none(self, settings):
        from isik.django.apps.inbound import conf

        isik_areas(settings).INBOUND = ["SOURCES"]

        assert conf.configured() == {}
        assert conf.setting("MAX_ATTEMPTS") is None
        assert conf.setting("BACKOFF") is None

    def test_partitioned_takes_both_hooks(self, settings):
        from isik.django.apps.inbound import conf

        isik_areas(settings).INBOUND = {"PARTITIONS": list}
        assert conf.partitioned() is False
        isik_areas(settings).INBOUND = {"IN_PARTITION": "contextlib.nullcontext"}
        assert conf.partitioned() is False
        isik_areas(settings).INBOUND = {"PARTITIONS": list, "IN_PARTITION": "contextlib.nullcontext"}
        assert conf.partitioned() is True


class TestTheSwappableSetting:
    def test_given_its_default_unless_the_area_names_a_model(self, settings):
        from isik.django.apps.inbound import swapping

        del settings.ISIK_INBOUND_EVENT_MODEL
        swapping.set_defaults()
        assert settings.ISIK_INBOUND_EVENT_MODEL == "isik_inbound.InboundEvent"

        del settings.ISIK_INBOUND_EVENT_MODEL
        isik_areas(settings).INBOUND = {"EVENT_MODEL": "testapp.OwnEvent"}
        swapping.set_defaults()
        assert settings.ISIK_INBOUND_EVENT_MODEL == "testapp.OwnEvent"


class TestTheUrls:
    def test_one_pattern_per_source_by_name(self):
        from django.urls import resolve, reverse

        from isik.django.apps.inbound.views import inbound_urls, receive

        (pattern,) = inbound_urls()
        assert (str(pattern.pattern), pattern.callback, pattern.name) == (
            "<str:source>/",
            receive,
            "isik-inbound-receive",
        )
        assert reverse("isik-inbound-receive", kwargs={"source": "acme"}) == "/in/acme/"
        assert resolve("/in/acme/").kwargs == {"source": "acme"}


class TestTheSignature:
    def request(self, body=b"{}", **headers):
        from django.test import RequestFactory

        return RequestFactory().post("/in/x/", body, content_type="application/json", headers=headers)

    def signer(self, secrets=None):
        return IsikSignature(
            header="X-Sig", secrets=secrets or (lambda request: ["s"]), tolerance=timedelta(seconds=300)
        )

    def test_keeps_what_its_given(self):
        secrets = lambda request: ["s"]  # noqa: E731
        signer = IsikSignature(header="X-Sig", secrets=secrets, tolerance=timedelta(seconds=1))

        assert (signer.header, signer.secrets, signer.tolerance) == ("X-Sig", secrets, timedelta(seconds=1))

    def test_its_secrets_are_the_requests(self, monkeypatch):
        import hashlib
        import hmac

        monkeypatch.setattr(sources.time, "time", lambda: 1000)
        signature = hmac.new(b"tenant-secret", b"1000.{}", hashlib.sha256).hexdigest()
        signer = self.signer(lambda request: [request.headers["X-Tenant-Secret"]])

        signer.verify(self.request(**{"X-Sig": f"t=1000,v1={signature}", "X-Tenant-Secret": "tenant-secret"}))

    def test_exactly_its_tolerance_old_is_in_time(self, monkeypatch):
        import hashlib
        import hmac

        monkeypatch.setattr(sources.time, "time", lambda: 1300)
        signature = hmac.new(b"s", b"1000.{}", hashlib.sha256).hexdigest()

        self.signer().verify(self.request(**{"X-Sig": f"t=1000,v1={signature}"}))

    def test_a_signature_carrying_another_equals_sign_is_one_that_doesnt_match(self, monkeypatch):
        monkeypatch.setattr(sources.time, "time", lambda: 1000)

        with pytest.raises(sources.Unverified) as refused:
            self.signer().verify(self.request(**{"X-Sig": "t=1000,v1=abc=def"}))

        assert str(refused.value) == "X-Sig matches none of the source's secrets."


class TestTheSharedSecret:
    def request(self, **headers):
        from django.test import RequestFactory

        return RequestFactory().post("/in/x/", b"{}", content_type="application/json", headers=headers)

    def verifier(self, secrets=None):
        return SharedSecret(header="Authorization", secrets=secrets or (lambda request: ["", "old", "new"]))

    def test_keeps_what_its_given(self):
        secrets = lambda request: ["s"]  # noqa: E731
        verifier = SharedSecret(header="X-Secret", secrets=secrets)

        assert (verifier.header, verifier.secrets) == ("X-Secret", secrets)

    @pytest.mark.parametrize("secret", ["old", "new"])
    def test_any_of_its_secrets(self, secret):
        self.verifier().verify(self.request(Authorization=secret))

    def test_its_secrets_are_the_requests(self):
        verifier = self.verifier(lambda request: [request.headers["X-Tenant"]])

        verifier.verify(self.request(Authorization="tenant-secret", **{"X-Tenant": "tenant-secret"}))

    @pytest.mark.parametrize("given", ["neither", "old ", "Bearer old"])
    def test_another_value(self, given):
        with pytest.raises(sources.Unverified) as refused:
            self.verifier().verify(self.request(Authorization=given))

        assert str(refused.value) == "Authorization matches none of the source's secrets."

    def test_an_empty_secret_matches_nothing(self):
        with pytest.raises(sources.Unverified):
            self.verifier(lambda request: [""]).verify(self.request(Authorization=" "))

    def test_none_sent(self):
        with pytest.raises(sources.Unverified) as refused:
            self.verifier().verify(self.request())

        assert str(refused.value) == "The request carries no Authorization."

    def test_a_value_outside_ascii(self):
        self.verifier(lambda request: ["ş"]).verify(self.request(Authorization="ş"))
