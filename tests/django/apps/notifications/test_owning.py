"""What makes the ledger the project's to own, piece by piece - the swapped project shows it whole."""

import types

from django.apps import apps

from isik.django.apps.notifications import checks, drf, swapping
from isik.django.apps.notifications.audience.models import Subscription
from isik.django.apps.notifications.models import Delivery, DeliveryAttempt, Notice


def test_defaults_are_given_only_where_unset(settings):
    del settings.ISIK_NOTIFICATIONS_NOTICE_MODEL
    settings.ISIK_NOTIFICATIONS_DELIVERY_MODEL = "testapp.Parcel"

    swapping.set_defaults()

    assert settings.ISIK_NOTIFICATIONS_NOTICE_MODEL == "isik_notifications.Notice"
    assert settings.ISIK_NOTIFICATIONS_DELIVERY_MODEL == "testapp.Parcel"


def test_an_index_is_named_after_its_table():
    assert [index.name for index in Notice._meta.indexes] == ["isik_notice_unfanned"]


def test_a_kind_field_keeps_what_its_given():
    assert drf.KindField(required=False, allow_null=True).allow_null is True


def test_a_delivery_is_read_only_attempts_and_all():
    serializer = drf.delivery_serializer.__wrapped__(Delivery, DeliveryAttempt)()
    attempts = serializer.fields["attempts"].child

    assert [name for name, field in serializer.fields.items() if not field.read_only] == []
    assert [name for name, field in attempts.fields.items() if not field.read_only] == []


GUARDED = (
    "ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL",
    "ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL",
    "ISIK_NOTIFICATIONS_ENDPOINT_KIND_MODEL",
)


class TestOwningThePolicy:
    def test_isiks_own_throughout_is_fine(self):
        assert checks._kept_guard_problems() == []

    def test_its_guarded_models_are_owned_with_it(self, settings):
        settings.ISIK_NOTIFICATIONS_KIND_POLICY_MODEL = "testapp.Parcel"
        settings.ISIK_NOTIFICATIONS_INBOX_ITEM_MODEL = "testapp.Parcel"

        assert checks._kept_guard_problems() == [
            "ISIK_NOTIFICATIONS_KIND_POLICY_MODEL is the project's, and ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL, "
            "ISIK_NOTIFICATIONS_ENDPOINT_KIND_MODEL still isik's - whose shipped guard reads isik's policy table. "
            "Own those too."
        ]
        assert checks._kept_guard_problems()[0] in checks.setting_problems()

    def test_owning_them_all_is_fine(self, settings):
        settings.ISIK_NOTIFICATIONS_KIND_POLICY_MODEL = "testapp.Parcel"
        for setting in GUARDED:
            setattr(settings, setting, "testapp.Parcel")

        assert checks._kept_guard_problems() == []

    def test_without_the_audience_app_theres_nothing_kept(self, settings, monkeypatch):
        settings.ISIK_NOTIFICATIONS_KIND_POLICY_MODEL = "testapp.Parcel"
        monkeypatch.setattr(apps, "is_installed", lambda name: name == checks.LEDGER_APP)

        assert checks._kept_guard_problems() == []


def test_a_swapped_out_guard_stays_as_shipped(settings, monkeypatch):
    def configured():
        raise AssertionError("a swapped-out guard reads no configured model")

    monkeypatch.setattr(swapping, "get_kind_policy_model", configured)
    settings.ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL = "testapp.Parcel"
    guard = next(trigger for trigger in Subscription._meta.triggers if trigger.name == "guard_variant")

    assert 'FROM "isik_notice_kind_policy" WHERE "id" = NEW."kind_id"' in guard.get_func(Subscription)


def test_a_kept_guard_reads_the_configured_policy(monkeypatch):
    class Policy:
        class _meta:
            db_table = "owned_policy"

            @staticmethod
            def get_field(name):
                return types.SimpleNamespace(column=f"owned_{name}")

    monkeypatch.setattr(swapping, "get_kind_policy_model", lambda: Policy)
    guard = next(trigger for trigger in Subscription._meta.triggers if trigger.name == "guard_variant")

    assert 'FROM "owned_policy" WHERE "owned_id" = NEW."kind_id"' in guard.get_func(Subscription)
