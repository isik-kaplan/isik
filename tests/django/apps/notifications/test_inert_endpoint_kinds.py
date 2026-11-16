"""
An endpoint's subscription to a kind not sent on webhook: refused when written, kept and inert when the kind's
policy moves to never - fan-out sends nothing on it - and alive again when it moves back; the endpoint's owner told
of each move, and the inert rows shown.
"""

import dataclasses
import json
from io import StringIO

import pgtrigger
import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection

from isik.django.apps.notifications import kinds as kinds_module
from isik.django.apps.notifications.audience.swapping import get_endpoint_kind_model, get_endpoint_model
from isik.django.apps.notifications.channels import Channel
from isik.django.apps.notifications.models import Policy
from isik.django.apps.notifications.signals import endpoint_kinds_restored, endpoint_kinds_withdrawn
from isik.django.apps.notifications.sync import mirror_kinds, sent_on_webhook, subscribed_endpoints
from tests.django.apps.notifications import kind_named
from tests.django.apps.notifications.test_pipeline import deliveries, fan_out, flag, redeclared


pytestmark = pytest.mark.django_db

NEVER_ON_WEBHOOK = dict.fromkeys((Channel.INBOX, Channel.EMAIL), Policy.DEFAULT_OFF) | {Channel.WEBHOOK: Policy.NEVER}
GUARD = "isik_notifications_audience.EndpointKind:guard_variant"


@pytest.fixture
def endpoint():
    return get_endpoint_model().objects.create(url="https://hooks.example/a", secret="s")


@pytest.fixture
def heard():
    """Every endpoint_kinds_withdrawn and endpoint_kinds_restored sent, as (signal, sender, kind, endpoint ids)."""
    sent = []

    def receiver(signal, sender, kind, endpoint_ids, **kwargs):
        sent.append((signal, sender, kind, endpoint_ids))

    endpoint_kinds_withdrawn.connect(receiver)
    endpoint_kinds_restored.connect(receiver)
    yield sent
    endpoint_kinds_withdrawn.disconnect(receiver)
    endpoint_kinds_restored.disconnect(receiver)


def flipped(monkeypatch, django_capture_on_commit_callbacks, channels):
    """The mirror run with parcel_flagged declared on `channels`, its commit's callbacks run."""
    with redeclared(monkeypatch, "parcel_flagged", channels=channels), django_capture_on_commit_callbacks(execute=True):
        mirror_kinds()


class TestWritingASubscription:
    def test_to_a_kind_never_sent_on_webhook_is_refused_by_the_database(self, endpoint):
        with pytest.raises(IntegrityError, match="EndpointKind: the kind is not sent on webhook"):
            get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_reminder"))

    def test_in_sql_too(self, endpoint):
        table = get_endpoint_kind_model()._meta.db_table
        with pytest.raises(IntegrityError, match="the kind is not sent on webhook"), connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} (id, endpoint_id, kind_id) VALUES (gen_random_uuid(), %s, %s)",
                [endpoint.pk, kind_named("parcel_reminder").pk],
            )

    def test_moved_onto_such_a_kind_is_refused(self, endpoint):
        row = get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))

        with pytest.raises(IntegrityError, match="the kind is not sent on webhook"):
            get_endpoint_kind_model().objects.filter(pk=row.pk).update(kind=kind_named("parcel_reminder"))

    def test_to_a_kind_with_no_webhook_policy_at_all_is_refused(self, endpoint):
        policy = kind_named("parcel_flagged")
        policy.channels = {Channel.INBOX: Policy.MANDATORY}
        policy.save()

        with pytest.raises(IntegrityError, match="the kind is not sent on webhook"):
            get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=policy)

    def test_to_a_kind_sent_on_webhook_goes_through(self, endpoint):
        get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))

        assert subscribed_endpoints(kind_named("parcel_flagged")) == [str(endpoint.pk)]


class TestAFlip:
    def test_keeps_the_rows_inert_and_back_again_they_send(
        self, monkeypatch, alice, endpoint, dispatched, django_capture_on_commit_callbacks
    ):
        get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))

        flipped(monkeypatch, django_capture_on_commit_callbacks, NEVER_ON_WEBHOOK)
        with redeclared(monkeypatch, "parcel_flagged", channels=NEVER_ON_WEBHOOK):
            withdrawn = flag(alice)
            fan_out(django_capture_on_commit_callbacks)
        assert get_endpoint_kind_model().objects.filter(endpoint=endpoint).count() == 1
        assert deliveries(withdrawn) == []

        flipped(monkeypatch, django_capture_on_commit_callbacks, kind_channels())
        restored = flag(alice)
        fan_out(django_capture_on_commit_callbacks)
        assert deliveries(restored) == [("webhook", f"endpoint:{endpoint.pk}")]

    def test_tells_of_each_move_once_its_mirror_commits(
        self, monkeypatch, endpoint, heard, django_capture_on_commit_callbacks
    ):
        other = get_endpoint_model().objects.create(url="https://hooks.example/b", secret="s")
        for each in (endpoint, other):
            get_endpoint_kind_model().objects.create(endpoint=each, kind=kind_named("parcel_flagged"), variant="lost")
        get_endpoint_kind_model().objects.create(
            endpoint=endpoint, kind=kind_named("parcel_flagged"), variant="damaged"
        )
        ids = sorted([str(endpoint.pk), str(other.pk)])
        Policies = type(kind_named("parcel_flagged"))

        with redeclared(monkeypatch, "parcel_flagged", channels=NEVER_ON_WEBHOOK):
            with django_capture_on_commit_callbacks() as callbacks:
                mirror_kinds()
            assert heard == []
            for callback in callbacks:
                callback()
        assert heard == [(endpoint_kinds_withdrawn, Policies, "parcel_flagged", ids)]

        flipped(monkeypatch, django_capture_on_commit_callbacks, NEVER_ON_WEBHOOK)
        assert len(heard) == 1

        flipped(monkeypatch, django_capture_on_commit_callbacks, kind_channels())
        assert heard[1:] == [(endpoint_kinds_restored, Policies, "parcel_flagged", ids)]

    def test_with_no_endpoints_tells_no_one(self, monkeypatch, heard, django_capture_on_commit_callbacks):
        flipped(monkeypatch, django_capture_on_commit_callbacks, NEVER_ON_WEBHOOK)
        flipped(monkeypatch, django_capture_on_commit_callbacks, kind_channels())

        assert heard == []

    def test_a_kind_new_to_the_mirror_tells_no_one(self, monkeypatch, heard, django_capture_on_commit_callbacks):
        flagged = next(kind for kind in kinds_module.declared if kind.name == "parcel_flagged")
        monkeypatch.setattr(
            kinds_module, "declared", [*kinds_module.declared, dataclasses.replace(flagged, name="new")]
        )

        with django_capture_on_commit_callbacks(execute=True):
            mirror_kinds()

        assert kind_named("new").channels == dict(flagged.policies)
        assert heard == []


def kind_channels():
    return dict.fromkeys((Channel.INBOX, Channel.EMAIL), Policy.DEFAULT_OFF) | {Channel.WEBHOOK: Policy.MANDATORY}


class TestSeeingThem:
    def inert(self, endpoint):
        with pgtrigger.ignore(GUARD):
            get_endpoint_kind_model().objects.create(
                endpoint=endpoint, kind=kind_named("parcel_reminder"), variant="daily"
            )

    def test_the_notices_command_lists_each_kind_s(self, endpoint):
        self.inert(endpoint)
        out = StringIO()
        call_command("notices", stdout=out)
        listed = StringIO()
        call_command("notices", "--format", "json", stdout=listed)

        assert (
            f"parcel_reminder isn't sent on webhook - 1 endpoints' subscriptions to it are inert: {endpoint.pk}"
            in out.getvalue()
        )
        assert json.loads(listed.getvalue())["inert"] == {"parcel_reminder": [str(endpoint.pk)]}

    def test_a_kind_sent_on_webhook_or_with_no_subscriptions_isn_t_listed(self, endpoint):
        get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))
        listed = StringIO()
        call_command("notices", "--format", "json", stdout=listed)

        assert json.loads(listed.getvalue())["inert"] == {}

    def test_a_policy_sends_on_webhook_unless_never_or_unnamed(self):
        assert sent_on_webhook({"webhook": "mandatory"})
        assert not sent_on_webhook({"webhook": "never"})
        assert not sent_on_webhook({"inbox": "mandatory"})


def test_with_no_audience_app_no_endpoint_subscribes(monkeypatch, endpoint):
    from isik.django.apps.notifications import sync

    get_endpoint_kind_model().objects.create(endpoint=endpoint, kind=kind_named("parcel_flagged"))
    monkeypatch.setattr(sync.apps, "is_installed", lambda app: app != "isik.django.apps.notifications.audience")

    assert subscribed_endpoints(kind_named("parcel_flagged")) == []
