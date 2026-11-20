"""Declaring sources and handlers, and isik.E028 - an inbound setting or declaration that can't work."""

from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured

from isik.django.apps.inbound import checks, sources
from isik.django.apps.inbound.checks import inbound_can_work, setting_problems, source_problems
from isik.django.apps.inbound.choices import PayloadKept
from isik.django.apps.inbound.sources import (
    IsikSignature,
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

        with pytest.raises(ImproperlyConfigured, match="A source called billing is declared already."):
            a_source()

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

        with pytest.raises(ImproperlyConfigured, match="billing's paid events have a handler already."):
            handles("billing", "paid")(print)
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
