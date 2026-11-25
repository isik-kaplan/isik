"""manage.py notices, the tasks and their beat schedule, and the shared TRUNCATE protection."""

import json
from io import StringIO

import pgtrigger
import pytest
from django.core.management import CommandError, call_command
from django.db import ProgrammingError, connection
from django.test import TransactionTestCase

from isik.django.apps.common.db import (
    ProtectTruncate,
    allow_truncate,
    flushing_through_truncate_protection,
    truncate_protections,
)
from isik.django.apps.notifications import pipeline, tasks
from isik.django.apps.notifications.models import NoticeKindPolicy
from isik.django.conf import isik_areas


def notices(*args):
    out = StringIO()
    call_command("notices", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestTheNoticesCommand:
    def test_it_lists_each_kind(self):
        listed = notices()

        assert (
            "parcel_flagged\n  source: trigger\n  model: testapp.Parcel\n  trigger: notify_parcel_flagged\n" in listed
        )
        assert "  variants: lost, damaged\n  audience: Field('sender')\n  scope: single_partition\n" in listed
        assert (
            "  channels: inbox=default_off, email=default_off, webhook=mandatory\n  snapshot: status, sender\n"
            in listed
        )
        assert "parcel_reminder\n  source: application\n  variants: daily, weekly\n  audience: Subscribers\n" in listed

    def test_it_lists_them_as_json(self):
        listed = json.loads(notices("--format", "json"))

        shipped = next(kind for kind in listed["kinds"] if kind["kind"] == "parcel_shipped")
        assert shipped["condition"] == "(NEW.\"status\" = 'shipped' AND NOT (OLD.\"status\" = 'shipped'))"
        assert shipped["on"] == "UPDATE"
        assert listed["drift"] == {"unmirrored": [], "undeclared": [], "retired": []}

    def test_it_shows_where_the_mirror_differs(self):
        with pgtrigger.ignore("isik_notifications.NoticeKindPolicy:protect_policy_delete"):
            NoticeKindPolicy.objects.filter(name="parcel_reminder").delete()
        NoticeKindPolicy.objects.create(name="gone", scope="single_partition", source="trigger")
        NoticeKindPolicy.objects.filter(name="parcel_shipped").update(status="retired")

        listed = notices()

        assert "Declared, with no policy row - run migrate: parcel_reminder\n" in listed
        assert "A policy row no declaration makes - retire it: gone\n" in listed
        assert "Declared, and retired: parcel_shipped\n" in listed

    def test_it_retires_a_kind(self):
        assert notices("--retire", "parcel_shipped") == "Retired parcel_shipped.\n"

        assert NoticeKindPolicy.objects.get(name="parcel_shipped").status == "retired"
        with pytest.raises(CommandError, match="^No unretired kind is called parcel_shipped.$"):
            notices("--retire", "parcel_shipped")

    def test_a_retire_is_a_moment_in_its_history_and_never_undone(self):
        from django.db import transaction

        notices("--retire", "parcel_shipped")
        policy = NoticeKindPolicy.objects.get(name="parcel_shipped")

        (retired,) = NoticeKindPolicy.pgh_event_model.objects.filter(pgh_obj=policy, status="retired")
        assert retired.pgh_label == "update"
        with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot update"):
            NoticeKindPolicy.objects.filter(pk=policy.pk).update(status="active")

    def test_a_kind_is_never_written_retired(self):
        from django.db import transaction

        with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot insert"):
            NoticeKindPolicy.objects.create(
                name="born_retired", scope="single_partition", source="trigger", status="retired"
            )

    def test_a_retired_kinds_other_columns_still_change(self):
        NoticeKindPolicy.objects.filter(name="parcel_shipped").update(status="retired")

        assert NoticeKindPolicy.objects.filter(name="parcel_shipped").update(channels={"inbox": "mandatory"}) == 1


@pytest.mark.django_db
class TestTheTasks:
    def test_each_runs_its_pipeline_step(self, monkeypatch):
        called = []
        for name, value in [
            ("reap_stranded", {"deliveries": 0, "shards": 0}),
            ("prune_attempts", 2),
            ("prune_notices", 3),
        ]:
            monkeypatch.setattr(pipeline, name, lambda _value=value, _name=name: called.append(_name) or _value)

        assert tasks.reap_stranded() == {"deliveries": 0, "shards": 0}
        assert tasks.prune_attempts() == 2
        assert tasks.prune_notices() == 3
        assert called == ["reap_stranded", "prune_attempts", "prune_notices"]

    def test_a_full_batch_starts_another_fan_out(self, monkeypatch, settings, dispatched):
        isik_areas(settings).NOTIFICATIONS = {"BATCH_SIZE": 2}
        fanned = iter([2, 1])
        monkeypatch.setattr(pipeline, "fan_out", lambda: next(fanned))

        assert tasks.fan_out() == 2
        assert dispatched == [("fan_out", (), {})]
        assert tasks.fan_out() == 1
        assert len(dispatched) == 1

    def test_send_and_fan_out_shard_answer_the_state(self, monkeypatch):
        class Row:
            state = "sent"

        monkeypatch.setattr(pipeline, "send", lambda pk, triggered_by: Row() if pk == "x" else None)
        monkeypatch.setattr(pipeline, "run_shard", lambda pk, claimed_by: Row() if pk == 1 else None)

        assert (tasks.send("x"), tasks.send("y")) == ("sent", None)
        assert (tasks.fan_out_shard(1), tasks.fan_out_shard(2)) == ("sent", None)

    def test_they_are_named_for_isik(self):
        assert [task.name for task in (tasks.fan_out, tasks.send, tasks.fan_out_shard)] == [
            "isik.notifications.fan_out",
            "isik.notifications.send",
            "isik.notifications.fan_out_shard",
        ]


class TestTruncateProtection:
    def test_every_registered_one_is_found(self):
        assert set(truncate_protections()) >= {
            "isik_notifications.Notice:protect_notice_truncate",
            "isik_notifications.DeliveryAttempt:protect_attempt_truncate",
        }
        assert all(not uri.endswith(":append_only") for uri in truncate_protections())

    def test_its_function_refuses(self):
        assert ProtectTruncate(name="t").get_func(None) == (
            "RAISE EXCEPTION 'pgtrigger: Cannot truncate %', TG_TABLE_NAME; RETURN NULL;"
        )

    @pytest.mark.django_db
    def test_allowing_it_lets_one_through(self):
        with allow_truncate(), connection.cursor() as cursor:
            cursor.execute("TRUNCATE isik_delivery_attempt")

    @pytest.mark.django_db
    def test_the_flush_goes_through_while_its_open(self, monkeypatch):
        def flush(self):
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE isik_delivery_attempt")

        monkeypatch.setattr(TransactionTestCase, "_fixture_teardown", flush)

        with flushing_through_truncate_protection():
            TransactionTestCase("__init__")._fixture_teardown()
            assert TransactionTestCase._fixture_teardown is not flush

        assert TransactionTestCase._fixture_teardown is flush
        with pytest.raises(ProgrammingError, match="Cannot truncate"):
            TransactionTestCase("__init__")._fixture_teardown()

    def test_it_puts_the_flush_back_even_on_an_error(self, monkeypatch):
        flush = TransactionTestCase._fixture_teardown

        with pytest.raises(RuntimeError), flushing_through_truncate_protection():
            raise RuntimeError

        assert TransactionTestCase._fixture_teardown is flush
