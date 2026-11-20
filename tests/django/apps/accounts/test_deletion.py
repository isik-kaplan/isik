"""A person deleting their own account: asked for, cancelled by signing back in, swept, then erased or anonymized."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user
from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest
from django.test import Client, TestCase
from django.utils import timezone

from isik.django.apps.accounts import deletion, tasks
from isik.django.apps.accounts.deletion import cancel_pending_deletion, request_deletion, sweep_deletions
from isik.django.apps.accounts.signals import account_deleted, account_deletion_cancelled, account_deletion_requested
from isik.django.apps.features.operations import set_setting
from isik.django.apps.notifications.audience.helpers import subscribe
from isik.django.apps.notifications.audience.models import Subscription
from isik.django.conf import isik_areas
from tests.django.apps.notifications import kind_named
from tests.testapp.models import EmailUser, ForceableSubscription, UserOverrides, Widget, WidgetEvent


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice():
    return EmailUser.objects.create_user(username="alice", email="alice@example.com", password="pw", first_name="Al")


@pytest.fixture
def heard():
    sent = []

    def receiver(signal, sender, **kwargs):
        sent.append((signal, sender, kwargs))

    signals = (account_deletion_requested, account_deletion_cancelled, account_deleted)
    for signal in signals:
        signal.connect(receiver)
    yield sent
    for signal in signals:
        signal.disconnect(receiver)


def anonymizing(settings):
    isik_areas(settings).ACCOUNTS = {
        **isik_areas(settings).ACCOUNTS,
        "DELETION_BACKEND": "isik.django.apps.accounts.backends.Anonymize",
    }


class TestAsked:
    def test_deleted_after_deletion_after_and_signed_out(self, alice, heard):
        client = Client()
        client.force_login(alice)
        before = timezone.now()

        deletes_at = request_deletion(alice)

        alice.refresh_from_db()
        assert before + timedelta(days=14) <= deletes_at <= timezone.now() + timedelta(days=14)
        assert (alice.deletes_at, alice.is_pending_deletion) == (deletes_at, True)
        assert alice.session_epoch is not None
        request = HttpRequest()
        request.session = client.session
        assert get_user(request).is_authenticated is False
        assert heard == [(account_deletion_requested, EmailUser, {"user": alice, "deletes_at": deletes_at})]

    def test_asking_again_keeps_the_first_date_and_signs_out_again(self, alice):
        first = request_deletion(alice)
        epoch = alice.session_epoch

        assert request_deletion(alice) == first
        assert alice.session_epoch != epoch

    def test_until_then_a_session_is_signed_as_django_signs_it(self, alice):
        assert alice.get_session_auth_hash() == AbstractBaseUser.get_session_auth_hash(alice)
        request_deletion(alice)
        assert alice.get_session_auth_hash() != AbstractBaseUser.get_session_auth_hash(alice)
        assert alice.get_session_auth_hash() == alice.get_session_auth_hash()

    def test_none_pending(self, alice):
        assert (alice.deletes_at, alice.is_pending_deletion) == (None, False)


class TestCancelled:
    def test_by_signing_back_in(self, alice, heard):
        request_deletion(alice)
        heard.clear()

        assert cancel_pending_deletion(alice) is True
        alice.refresh_from_db()
        assert alice.deletes_at is None
        assert heard == [(account_deletion_cancelled, EmailUser, {"user": alice})]

    def test_only_the_pending_deletion_is_written(self, alice):
        request_deletion(alice)
        EmailUser.objects.filter(pk=alice.pk).update(first_name="Alice")

        cancel_pending_deletion(alice)

        assert EmailUser.objects.values_list("first_name", "deletes_at").get(pk=alice.pk) == ("Alice", None)

    def test_with_none_pending(self, alice, heard):
        assert cancel_pending_deletion(alice) is False
        assert heard == []


class TestSwept:
    def test_each_account_whose_time_has_come(self, alice):
        bob = EmailUser.objects.create(username="bob", email="bob@example.com")
        carol = EmailUser.objects.create(username="carol", email="carol@example.com")
        request_deletion(alice)
        request_deletion(bob)
        EmailUser.objects.filter(pk=bob.pk).update(deletes_at=timezone.now() + timedelta(days=30))

        assert sweep_deletions(timezone.now() + timedelta(days=15)) == 1
        assert set(EmailUser.objects.values_list("username", flat=True)) == {"bob", "carol"}
        assert carol.deletes_at is None

    def test_now_unless_told(self, alice):
        request_deletion(alice)
        EmailUser.objects.filter(pk=alice.pk).update(deletes_at=timezone.now() - timedelta(seconds=1))

        assert sweep_deletions() == 1

    def test_the_task_and_its_beat_entry(self, alice):
        assert tasks.sweep_deletions() == 0
        assert tasks.beat_schedule(3600) == {
            "isik.accounts.sweep_deletions": {"task": "isik.accounts.sweep_deletions", "schedule": 3600}
        }


class TestErased:
    def test_the_row_what_cascades_and_their_history(self, alice, heard):
        subscribe(alice, "parcel_shipped")
        forced = ForceableSubscription.objects.create(user=alice, kind=kind_named("parcel_shipped"))
        forced.enabled = False
        forced.save()
        set_setting(alice, "theme", "dark")
        assert ForceableSubscription.pgh_event_model.objects.filter(pgh_obj_id=forced.pk).exists()

        with TestCase.captureOnCommitCallbacks(execute=True):
            deletion.delete_account(alice)

        assert not EmailUser.objects.filter(pk=alice.pk).exists()
        assert not Subscription.objects.exists()
        assert not ForceableSubscription.objects.exists()
        assert not ForceableSubscription.pgh_event_model.objects.filter(pgh_obj_id=forced.pk).exists()
        assert not UserOverrides.objects.exists()
        assert heard == [(account_deleted, EmailUser, {"user_id": alice.pk})]

    def test_a_registered_models_rows_go_with_their_history(self, alice):
        widget = Widget.objects.create(name="Al's", owner=alice)
        others = Widget.objects.create(name="Bo's")

        deletion.delete_account(alice)

        assert list(Widget.objects.all()) == [others]
        assert not WidgetEvent.objects.filter(pgh_obj_id=widget.pk).exists()
        assert WidgetEvent.objects.filter(pgh_obj_id=others.pk).exists()


class TestAnonymized:
    def test_a_tombstone_left(self, alice, settings):
        anonymizing(settings)
        request_deletion(alice)
        signed_out_with = alice.session_epoch

        deletion.delete_account(alice)

        tombstone = EmailUser.objects.get(pk=alice.pk)
        assert (tombstone.email, tombstone.username, tombstone.first_name, tombstone.last_name) == (
            f"deleted-{alice.pk}@example.invalid",
            f"deleted-{alice.pk}",
            "",
            "",
        )
        assert (tombstone.has_usable_password(), tombstone.is_active, tombstone.deletes_at) == (False, False, None)
        assert tombstone.session_epoch not in (None, signed_out_with)

    def test_registered_rows_deleted_or_kept_with_their_fields_set_in_their_history_too(self, alice, settings):
        anonymizing(settings)
        widget = Widget.objects.create(name="Al's", owner=alice)
        widget.count = 3
        widget.save()
        subscribe(alice, "parcel_shipped")

        deletion.delete_account(alice)

        widget.refresh_from_db()
        assert (widget.owner_id, widget.name, widget.count) == (alice.pk, "a deleted person's widget of 3", 3)
        assert set(WidgetEvent.objects.filter(pgh_obj_id=widget.pk).values_list("name", flat=True)) == {
            "a deleted person's widget of 3"
        }
        assert not Subscription.objects.exists()

    def test_a_registration_naming_neither_leaves_its_rows_be(self, alice, settings, monkeypatch):
        from isik.django.apps.accounts import erasures

        anonymizing(settings)
        monkeypatch.setitem(erasures.erasures, "testapp.Widget", erasures.Erasure(model="testapp.Widget", via="owner"))
        widget = Widget.objects.create(name="Al's", owner=alice)

        deletion.delete_account(alice)

        widget.refresh_from_db()
        assert (widget.owner_id, widget.name) == (alice.pk, "Al's")

    def test_a_field_the_history_doesnt_copy_is_set_on_the_row_alone(self, alice):
        from isik.django.apps.accounts.backends import _scrub_history

        widget = Widget.objects.create(name="Al's", owner=alice)

        _scrub_history(Widget, widget.pk, {"not_a_column": "x"})

        assert set(WidgetEvent.objects.filter(pgh_obj_id=widget.pk).values_list("name", flat=True)) == {"Al's"}


class TestTheSessionHash:
    def test_signed_with_the_password_and_epoch(self, alice):
        import uuid

        from django.utils.crypto import salted_hmac

        alice.session_epoch = uuid.UUID(int=7)

        assert (
            alice.get_session_auth_hash()
            == salted_hmac(
                "isik.django.apps.accounts.bases.DeletableAccount.get_session_auth_hash",
                f"{alice.password}:{alice.session_epoch}",
                algorithm="sha256",
            ).hexdigest()
        )


class TestWrittenAlone:
    def test_asking_writes_only_the_deletion_and_epoch(self, alice):
        EmailUser.objects.filter(pk=alice.pk).update(first_name="Alice")

        request_deletion(alice)

        assert EmailUser.objects.get(pk=alice.pk).first_name == "Alice"


def test_the_user_rows_own_history_is_scrubbed_with_what_was_set(alice, settings, monkeypatch):
    from isik.django.apps.accounts import backends

    anonymizing(settings)
    scrubbed = []
    monkeypatch.setattr(backends, "_scrub_history", lambda model, pk, values: scrubbed.append((model, pk, values)))

    deletion.delete_account(alice)

    assert scrubbed == [
        (
            EmailUser,
            alice.pk,
            {
                "email": f"deleted-{alice.pk}@example.invalid",
                "username": f"deleted-{alice.pk}",
                "first_name": "",
                "last_name": "",
            },
        )
    ]
