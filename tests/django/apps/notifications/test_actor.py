"""Who acted, and the context to read them back by - on the notice, and on the inbox item it becomes."""

import pghistory
import pytest
from django.db import connection

from isik.django.apps.notifications import checks, pipeline, triggers
from isik.django.apps.notifications.audience.models import InboxItem
from isik.django.apps.notifications.drf import inbox_item_serializer
from isik.django.apps.notifications.kinds import raise_notice
from isik.django.apps.notifications.models import Delivery, Notice
from isik.django.apps.notifications.pipeline import record_email
from tests.testapp.models import PARCEL_RECEIPT, PARCEL_REMINDER


pytestmark = pytest.mark.django_db


def ship(parcel):
    parcel.status = "shipped"
    parcel.save()
    return Notice.objects.get(kind_id="parcel_shipped")


def test_a_trigger_records_who_acted_and_where(parcel, alice):
    with pghistory.context(user=alice.pk, schema="acme", why="testing"):
        notice = ship(parcel)

    assert (notice.actor_id, notice.context) == (str(alice.pk), {"user": alice.pk, "schema": "acme"})


def test_outside_a_context_nobody_acted(parcel):
    notice = ship(parcel)

    assert (notice.actor_id, notice.context) == (None, {})


def test_a_context_naming_no_one(parcel):
    with pghistory.context(why="a sweep"):
        notice = ship(parcel)

    assert (notice.actor_id, notice.context) == (None, {})


def test_an_emptied_context_is_none(parcel):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('pghistory.context_metadata', '', true)")
    notice = ship(parcel)

    assert (notice.actor_id, notice.context) == (None, {})


def test_a_raised_notice_records_them_alike(alice):
    with pghistory.context(user=alice.pk, schema="acme"):
        notice = raise_notice(PARCEL_REMINDER, variant="daily")

    assert (notice.actor_id, notice.context) == (str(alice.pk), {"user": alice.pk, "schema": "acme"})


def test_a_recorded_email_records_them_alike(alice, dispatched):
    with pghistory.context(user=alice.pk):
        delivery = record_email(PARCEL_RECEIPT, address="a@example.com", send=lambda: None)

    assert (delivery.notice.actor_id, delivery.notice.context) == (str(alice.pk), {"user": alice.pk})


def test_the_inbox_item_carries_them_and_doesnt_show_them(parcel, alice, bob, dispatched):
    with pghistory.context(user=alice.pk, schema="acme"):
        ship(parcel)
    pipeline.fan_out()
    pipeline.send(Delivery.objects.get(channel="inbox").pk)

    item = InboxItem.objects.get()
    assert (item.actor_id, item.context) == (str(alice.pk), {"user": alice.pk, "schema": "acme"})
    shown = inbox_item_serializer(InboxItem)(item).data
    assert "actor_id" not in shown
    assert "context" not in shown


class TestTheSQL:
    def test_no_actor_key_records_no_one(self, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "ACTOR_CONTEXT_KEY": None}

        assert triggers.actor() == "NULL"

    def test_no_context_keys_keep_nothing(self, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_CONTEXT_KEYS": ()}

        assert triggers.context() == "'{}'::jsonb"

    def test_the_keys_are_read_from_the_metadata(self):
        assert triggers.actor() == f"({triggers.METADATA} ->> 'user')"
        assert triggers.context() == (
            f"jsonb_strip_nulls(jsonb_build_object('user', {triggers.METADATA} -> 'user', "
            f"'schema', {triggers.METADATA} -> 'schema'))"
        )


@pytest.mark.parametrize(
    ("configured", "problem"),
    [
        ({"ACTOR_CONTEXT_KEY": 7}, "ISIK_NOTIFICATIONS' ACTOR_CONTEXT_KEY is a history context key, or None."),
        (
            {"NOTICE_CONTEXT_KEYS": "user"},
            'ISIK_NOTIFICATIONS\' NOTICE_CONTEXT_KEYS is history context keys, such as ("user",).',
        ),
        (
            {"NOTICE_CONTEXT_KEYS": ("user", 7)},
            'ISIK_NOTIFICATIONS\' NOTICE_CONTEXT_KEYS is history context keys, such as ("user",).',
        ),
    ],
)
def test_the_settings_are_checked(settings, configured, problem):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, **configured}

    assert checks.setting_problems() == [problem]
