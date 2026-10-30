"""manage.py notices, the tasks and their beat schedule, and the shared TRUNCATE protection."""

import gc
import json
import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO

import pgtrigger
import pytest
from celery import Celery
from django.core.management import CommandError, call_command
from django.db import ProgrammingError, connection
from django.test import TransactionTestCase
from django.utils import timezone

from isik.django.apps.common.db import (
    ProtectTruncate,
    allow_truncate,
    flushing_through_truncate_protection,
    truncate_protections,
)
from isik.django.apps.notifications import pipeline, schedule, tasks
from isik.django.apps.notifications.models import NoticeKindPolicy


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
        assert "  variants: lost, damaged\n  audience: Field('sender')\n  policy: default_off\n" in listed
        assert "  channels: inbox, email, webhook\n  snapshot: status, sender\n" in listed
        assert "parcel_reminder\n  source: application\n  variants: daily, weekly\n  audience: Subscribers\n" in listed

    def test_it_lists_them_as_json(self):
        listed = json.loads(notices("--format", "json"))

        shipped = next(kind for kind in listed["kinds"] if kind["kind"] == "parcel_shipped")
        assert shipped["condition"].startswith("<Q:")
        assert shipped["on"] == "UPDATE"
        assert listed["drift"] == {"unmirrored": [], "undeclared": [], "retired": []}

    def test_it_shows_where_the_mirror_differs(self):
        with pgtrigger.ignore("isik_notifications.NoticeKindPolicy:protect_policy_delete"):
            NoticeKindPolicy.objects.filter(pk="parcel_reminder").delete()
        NoticeKindPolicy.objects.create(kind="gone", policy="mandatory", scope="single_partition", source="trigger")
        NoticeKindPolicy.objects.filter(pk="parcel_shipped").update(retired_at=timezone.now())

        listed = notices()

        assert "Declared, with no policy row - run migrate: parcel_reminder\n" in listed
        assert "A policy row no declaration makes - retire it: gone\n" in listed
        assert "Declared, and retired: parcel_shipped\n" in listed

    def test_it_retires_a_kind(self):
        assert notices("--retire", "parcel_shipped") == "Retired parcel_shipped.\n"

        assert NoticeKindPolicy.objects.get(pk="parcel_shipped").retired_at is not None
        with pytest.raises(CommandError, match="^No unretired kind is called parcel_shipped.$"):
            notices("--retire", "parcel_shipped")


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
        settings.ISIK_NOTIFICATIONS = {"BATCH_SIZE": 2}
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

    def test_the_beat_schedule(self):
        assert schedule.beat_schedule() == {
            "isik.notifications.fan_out": {"task": "isik.notifications.fan_out", "schedule": timedelta(seconds=10)},
            "isik.notifications.reap_stranded": {
                "task": "isik.notifications.reap_stranded",
                "schedule": timedelta(minutes=1),
            },
            "isik.notifications.prune_attempts": {
                "task": "isik.notifications.prune_attempts",
                "schedule": timedelta(hours=1),
            },
            "isik.notifications.prune_notices": {
                "task": "isik.notifications.prune_notices",
                "schedule": timedelta(hours=1),
            },
        }
        assert schedule.beat_schedule(fan_out_every=1)["isik.notifications.fan_out"]["schedule"] == 1

    def test_the_beat_schedule_names_the_tasks(self):
        named = {entry["task"] for entry in schedule.beat_schedule().values()}

        assert named == {
            task.name for task in (tasks.fan_out, tasks.reap_stranded, tasks.prune_attempts, tasks.prune_notices)
        }

    def test_its_merged_once_the_app_is_configured(self):
        app = Celery("merging", set_as_current=False)
        app.conf.beat_schedule = {"renew": {"task": "app.renew", "schedule": 60}}

        schedule.merge_beat_schedule(app, fan_out_every=5)
        assert app.configured is False
        gc.collect()

        merged = app.conf.beat_schedule
        assert merged == {"renew": {"task": "app.renew", "schedule": 60}, **schedule.beat_schedule(fan_out_every=5)}

    def test_its_merged_into_an_app_with_no_schedule_of_its_own(self):
        app = Celery("merging", set_as_current=False)

        schedule.merge_beat_schedule(app)

        assert app.conf.beat_schedule == schedule.beat_schedule()

    def test_the_beat_schedule_is_imported_before_django_is_set_up(self):
        # What a project's celery.py does - before django.setup(), with no settings at all.
        run = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "from isik.django.apps.notifications.schedule import beat_schedule; beat_schedule()",
            ],
            capture_output=True,
            text=True,
            env={key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"},
        )

        assert run.returncode == 0, run.stderr


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
