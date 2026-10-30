"""
The notifications pipeline end to end with every model the project's own - the failure a project keeping
isik's defaults can't see: a table named by isik's default where the configured model belongs.
"""

import uuid
from datetime import timedelta

import pgtrigger
import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.utils import timezone

from isik.django.apps.notifications import pipeline, swapping, tasks
from isik.django.apps.notifications.audience import swapping as audience_swapping
from isik.django.apps.notifications.audience.helpers import subscribe
from isik.django.apps.notifications.checks import notification_problems
from isik.django.apps.notifications.models import DeliveryState
from tests_swapped.owned import models as owned


pytestmark = pytest.mark.django_db

GETTERS = {
    swapping.get_kind_policy_model: owned.KindPolicy,
    swapping.get_notice_model: owned.Notice,
    swapping.get_fan_out_shard_model: owned.FanOutShard,
    swapping.get_delivery_model: owned.Delivery,
    swapping.get_webhook_delivery_model: owned.WebhookDelivery,
    swapping.get_email_delivery_model: owned.EmailDelivery,
    swapping.get_inbox_delivery_model: owned.InboxDelivery,
    swapping.get_push_delivery_model: owned.PushDelivery,
    swapping.get_delivery_attempt_model: owned.DeliveryAttempt,
    audience_swapping.get_subscription_model: owned.Subscription,
    audience_swapping.get_inbox_item_model: owned.InboxItem,
    audience_swapping.get_endpoint_model: owned.Endpoint,
    audience_swapping.get_endpoint_kind_model: owned.EndpointKind,
    audience_swapping.get_device_model: owned.Device,
}


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch):
    for name in ("send", "fan_out_shard", "fan_out"):
        monkeypatch.setattr(getattr(tasks, name), "delay", lambda *args, **kwargs: None)


@pytest.fixture
def alice():
    return get_user_model().objects.create(username="alice", email="alice@example.com")


def lose(alice, state="stolen"):
    package = owned.Package.objects.create(owner=alice, label="Bob's & <box>")
    owned.Package.objects.filter(pk=package.pk).update(state=state)
    return owned.Notice.objects.get(subject_id=str(package.pk))


def test_every_getter_gives_the_projects_model():
    assert {getter: getter() for getter in GETTERS} == GETTERS


# Every table isik's own models would make, were they not the project's.
ISIKS_TABLES = [
    model._meta.db_table
    for app in ("isik_notifications", "isik_notifications_audience")
    for model in apps.get_app_config(app).get_models(include_swapped=True)
]


def test_isiks_own_tables_were_never_made():
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE tablename = ANY(%s)", [ISIKS_TABLES])
        assert cursor.fetchall() == []


def test_nothing_installed_names_isiks_tables():
    # Every trigger function and index in the database, read back from Postgres itself.
    pattern = "|".join(ISIKS_TABLES)
    with connection.cursor() as cursor:
        cursor.execute("SELECT proname FROM pg_proc WHERE prosrc ~ %s", [rf"\m({pattern})\M"])
        functions = cursor.fetchall()
        cursor.execute("SELECT indexname FROM pg_indexes WHERE indexdef ~ %s", [rf"\m({pattern})\M"])
        indexes = cursor.fetchall()
    assert (len(ISIKS_TABLES), functions, indexes) == (14, [], [])


def test_the_checks_have_nothing_to_say():
    assert notification_problems() == []


def test_a_change_writes_its_notice_to_the_projects_table(alice):
    notice = lose(alice)

    # The trigger's insert takes the project's own key, from its db_default.
    assert isinstance(notice.pk, uuid.UUID)

    assert (notice.kind_id, notice.variant, notice.snapshot) == (
        "package_lost",
        "stolen",
        {"label": "Bob's & <box>", "owner": alice.pk},
    )
    assert owned.KindPolicy.objects.get(pk="package_lost").variants == ["stolen", "misplaced"]


def test_a_notice_fans_out_and_is_sent_into_the_projects_tables(alice):
    notice = lose(alice)

    assert pipeline.fan_out() == 1

    deliveries = owned.Delivery.objects.filter(notice=notice)
    assert sorted(deliveries.values_list("channel", flat=True)) == ["email", "inbox"]
    assert owned.InboxDelivery.objects.get().user_id == str(alice.pk)
    assert owned.EmailDelivery.objects.get().address == "alice@example.com"
    for delivery in deliveries:
        assert pipeline.send(delivery.pk).state == DeliveryState.SENT
    assert owned.InboxItem.objects.get().kind_id == "package_lost"
    assert mail.outbox[0].body == "Bob's & <box> is lost\n"


def test_a_subscription_is_guarded_in_the_projects_table(alice):
    subscribe(alice, "package_lost", "stolen")

    with transaction.atomic(), pytest.raises(IntegrityError, match="declares no such variant"):
        owned.Subscription.objects.create(user=alice, kind_id="package_lost", variant="burnt")
    with transaction.atomic(), pytest.raises(IntegrityError, match="no kind is called that"):
        owned.Subscription.objects.create(user=alice, kind_id="nothing")


def test_opting_out_reaches_nobody(alice):
    subscribe(alice, "package_lost", enabled=False)
    notice = lose(alice)

    pipeline.fan_out()

    assert not owned.Delivery.objects.filter(notice=notice).exists()


def test_the_projects_notice_and_attempts_are_protected(alice):
    notice = lose(alice)
    with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot update rows from owned_notice"):
        owned.Notice.objects.filter(pk=notice.pk).update(variant="misplaced")
    with pytest.raises(ProgrammingError, match="Cannot truncate"), connection.cursor() as cursor:
        cursor.execute("TRUNCATE owned_delivery_attempt")


def test_the_projects_kind_policy_refuses_a_truncate_as_it_refuses_a_delete():
    with pytest.raises(ProgrammingError, match="Cannot truncate"), connection.cursor() as cursor:
        cursor.execute("TRUNCATE owned_kind_policy CASCADE")


def test_a_retry_and_pruning_on_the_projects_tables(alice, settings):
    settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "NOTICE_RETENTION": timedelta(0)}
    notice = lose(alice)
    pipeline.fan_out()
    delivery = owned.Delivery.objects.get(notice=notice, channel="inbox")
    owned.Delivery.objects.filter(pk=delivery.pk).update(state=DeliveryState.ABANDONED, attempt_count=1)

    assert pipeline.retry_delivery(delivery).state == DeliveryState.PENDING
    for each in owned.Delivery.objects.filter(notice=notice):
        pipeline.send(each.pk)
    owned.DeliveryAttempt.objects.create(
        delivery=delivery,
        attempt_no=9,
        succeeded=True,
        started_at=timezone.now(),
        duration_ms=1,
        triggered_by="send",
    )

    assert pipeline.prune_notices() == 1
    assert not owned.Notice.objects.exists()
    assert not owned.DeliveryAttempt.objects.exists()


def test_the_reaper_and_shards_on_the_projects_tables(alice):
    notice = lose(alice)
    long_ago = timezone.now() - timedelta(hours=1)
    owned.FanOutShard.objects.create(
        notice=notice, partition=None, state="claimed", claimed_at=long_ago, next_attempt_at=long_ago
    )

    assert pipeline.reap_stranded() == {"deliveries": 0, "shards": 1}
    assert pipeline.oldest_unfanned_notice_age() is not None


def test_an_endpoint_hears_only_its_owners_notices(alice):
    bob = get_user_model().objects.create(username="bob", email="bob@example.com")
    for owner in (alice, bob):
        endpoint = owned.Endpoint.objects.create(url=f"https://{owner.username}.example/in", secret="s", owner=owner)
        owned.EndpointKind.objects.create(endpoint=endpoint, kind_id="package_lost")

    notice = lose(alice)
    pipeline.fan_out()

    [heard] = owned.WebhookDelivery.objects.filter(delivery__notice=notice)
    assert owned.Endpoint.objects.get(pk=heard.endpoint_id).owner == alice


def test_a_webhook_endpoint_is_the_projects(alice):
    endpoint = owned.Endpoint.objects.create(url="https://hooks.example/in", secret=uuid.uuid4().hex)
    owned.EndpointKind.objects.create(endpoint=endpoint, kind_id="package_lost")

    with pgtrigger.ignore():
        assert owned.EndpointKind.objects.get().endpoint == endpoint


def test_isiks_migrations_match_the_models_it_ships():
    # isik's own models are swapped out here, and their migrations stay the ones isik shipped.
    call_command("makemigrations", check=True, dry_run=True, skip_checks=True, verbosity=0)


def test_a_push_goes_to_the_projects_device_through_its_own_tables(alice, settings, monkeypatch):
    import json

    import httpx
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from isik.django.apps.notifications.audience.helpers import register_device
    from isik.django.apps.notifications.channels import push

    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    settings.ISIK_NOTIFICATIONS = {
        **settings.ISIK_NOTIFICATIONS,
        "CHANNELS": ("inbox", "email", "webhook", "push"),
        "PUSH": {"APNS": {"TEAM_ID": "T", "KEY_ID": "K", "KEY": key.decode(), "TOPIC": "com.owned.app"}},
    }
    sent = []
    monkeypatch.setattr(
        push,
        "client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200))
        ),
    )
    device = register_device(alice, "apns", "b" * 64)
    notice = lose(alice)

    pipeline.fan_out()
    delivery = owned.Delivery.objects.get(notice=notice, channel="push")

    assert owned.PushDelivery.objects.get().device_id == str(device.pk)
    assert isinstance(device, owned.Device)
    assert pipeline.send(delivery.pk).state == DeliveryState.SENT
    assert json.loads(sent[0].content)["aps"]["alert"] == {"title": "Package lost", "body": "Bob's & <box> is lost."}


@pytest.mark.parametrize("app_label", ["isik_notifications", "isik_notifications_audience", "isik_features"])
def test_isiks_upgrade_runs_before_the_projects_app_is_in_the_state(app_label):
    # What migrating from empty meets when the project's app comes after isik's in the plan: the state up to
    # isik's 0001 holds isik's swapped models keying an app it doesn't hold yet. Rendering it raises.
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection)
    (operation,) = loader.get_migration(app_label, "0002_isik_names").operations
    state = loader.project_state((app_label, "0001_initial"))

    with connection.schema_editor() as editor:
        operation.database_forwards(app_label, editor, state, state)

    assert "apps" not in state.__dict__
