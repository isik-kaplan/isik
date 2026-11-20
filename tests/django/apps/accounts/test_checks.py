"""Registering a person's rows, and isik.E029 - deleting an account that can't work, or would leave their rows."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.accounts import checks, conf, erasures
from isik.django.apps.accounts.backends import Anonymize, Erase
from isik.django.apps.accounts.checks import account_problems, accounts_can_work, setting_problems
from isik.django.apps.accounts.erasures import erasure, import_erasures_module, register_isiks_own
from isik.django.conf import isik_areas
from tests.testapp.models import EmailUser, Parcel


AREA = "ISIK['DJANGO']['ACCOUNTS']"


@pytest.fixture
def registered(monkeypatch):
    monkeypatch.setattr(erasures, "erasures", dict(erasures.erasures))
    monkeypatch.setattr(checks, "erasures", erasures.erasures)
    return erasures.erasures


def accounts(settings, **given):
    isik_areas(settings).ACCOUNTS = {"DELETION_AFTER": timedelta(days=14), **given}


class TestRegistering:
    def test_by_its_key_to_the_user(self, registered):
        found = erasure("testapp.ForceableSubscription", delete=True)

        assert (found.lookup(), found.delete, found.anonymize) == ("user", True, {})
        assert registered["testapp.ForceableSubscription"] is found

    def test_nothing_deleted_unless_asked(self, registered):
        found = erasure("testapp.Widget", via="owner", anonymize={"name": ""})

        assert (found.delete, found.anonymize) == (False, {"name": ""})

    def test_isiks_own_with_every_app_installed(self, registered):
        registered.clear()

        register_isiks_own()

        assert {label: (entry.via, entry.delete) for label, entry in registered.items()} == {
            "isik_notifications_audience.Subscription": (None, True),
            "isik_notifications_audience.InboxItem": (None, True),
            "isik_notifications_audience.Device": (None, True),
            "idempotency_by_reference.IdempotencyClaim": ("claimed_by", True),
            "idempotency_with_body.IdempotencyClaimWithBody": ("claimed_by", True),
        }

    def test_by_class_or_label_alike(self, registered):
        assert erasure(EmailUser).model == "testapp.EmailUser"
        assert erasure(EmailUser).lookup() == "pk"

    def test_by_the_lookup_it_names(self, registered):
        assert erasure(Parcel, via="sender", delete=True).lookup() == "sender"

    def test_one_with_two_keys_names_neither_by_itself(self, registered):
        with pytest.raises(ImproperlyConfigured, match="testapp.Parcel has 2 keys to the user model - name the"):
            erasure(Parcel, delete=True).lookup()

    def test_deleted_or_anonymized_not_both(self, registered):
        with pytest.raises(ImproperlyConfigured) as refused:
            erasure("testapp.Widget", delete=True, anonymize={"name": ""})

        assert str(refused.value) == (
            "testapp.Widget's rows are deleted or anonymized, not both - give delete=True or anonymize."
        )

    def test_isiks_own_are_registered_for_each_app_installed(self, registered, monkeypatch):
        registered.clear()
        installed = {"isik.django.apps.idempotency.with_body"}
        monkeypatch.setattr(erasures.apps, "is_installed", lambda app: app in installed)

        register_isiks_own()

        assert {label: entry.via for label, entry in registered.items()} == {
            "idempotency_with_body.IdempotencyClaimWithBody": "claimed_by"
        }

    def test_the_module_is_imported_where_its_named(self, settings, monkeypatch):
        imported = []
        monkeypatch.setattr(erasures, "import_module", imported.append)
        accounts(settings, ERASURES="users.erasures")
        import_erasures_module()
        accounts(settings)
        import_erasures_module()

        assert imported == ["users.erasures"]


class TestTheSettings:
    def test_the_test_projects_are_valid(self):
        assert account_problems() == []
        assert accounts_can_work(None) == []

    def test_keys_it_doesnt_know(self, settings):
        accounts(settings, COLOR="red", SIZE=1)

        assert setting_problems() == [f"{AREA} has keys it doesn't know: COLOR, SIZE."]

    def test_an_area_that_isnt_a_dict_is_none(self, settings):
        isik_areas(settings).ACCOUNTS = ["DELETION_AFTER"]

        assert conf.configured() == {}

    def test_anonymizing_with_the_user_registered(self, settings):
        accounts(settings, DELETION_BACKEND=Anonymize)

        assert setting_problems() == []

    def test_erasing_with_nothing_said_of_the_user(self, settings, registered):
        del registered["testapp.EmailUser"]

        assert setting_problems() == []

    @pytest.mark.parametrize("after", [None, 14, timedelta(seconds=-1)])
    def test_a_deletion_after_that_isnt_one(self, settings, after):
        accounts(settings, DELETION_AFTER=after)

        assert setting_problems() == [
            f"{AREA}['DELETION_AFTER'] is how long a person has to change their mind, a timedelta."
        ]

    def test_at_once(self, settings):
        accounts(settings, DELETION_AFTER=timedelta(0))

        assert setting_problems() == []

    def test_a_backend_given_as_its_class(self, settings):
        accounts(settings, DELETION_BACKEND=Erase)

        assert isinstance(conf.deletion_backend(), Erase)
        assert setting_problems() == []

    def test_a_backend_that_cant_be_made(self, settings):
        accounts(settings, DELETION_BACKEND="billing.backends.Nowhere")

        (problem,) = setting_problems()
        assert problem.startswith(f"{AREA}['DELETION_BACKEND'] can't be made: ")

    def test_a_backend_without_delete(self, settings):
        accounts(settings, DELETION_BACKEND=object)

        assert setting_problems() == [f"{AREA}['DELETION_BACKEND'] has no delete(user)."]

    def test_anonymizing_with_nothing_said_of_the_user(self, settings, registered):
        accounts(settings, DELETION_BACKEND=Anonymize)
        del registered["testapp.EmailUser"]

        assert setting_problems() == [
            f"{AREA} anonymizes an account, and nothing says what of testapp.EmailUser's to set - register it with "
            "erasure(testapp.EmailUser, anonymize={...})."
        ]


class TestTheRows:
    def test_a_registration_that_cant_find_its_rows(self, registered):
        erasure(Parcel, delete=True)
        erasure("testapp.Nowhere", delete=True)

        assert account_problems() == [
            "testapp.Parcel has 2 keys to the user model - name the person's with via=.",
            "App 'testapp' doesn't have a 'Nowhere' model.",
        ]

    @isolate_apps("tests.testapp")
    def test_a_key_that_would_block_or_outlive_a_deletion(self, registered, monkeypatch):
        from isik.django.apps.features.bases import ERASE

        class Receipt(models.Model):
            settled = models.ForeignKey(EmailUser, on_delete=ERASE, related_name="+")
            buyer = models.ForeignKey(EmailUser, on_delete=models.PROTECT, related_name="+")
            seller = models.ForeignKey(EmailUser, on_delete=models.DO_NOTHING, related_name="+")
            payer = models.ForeignKey(EmailUser, on_delete=models.CASCADE, related_name="+")
            note = models.ForeignKey(EmailUser, null=True, on_delete=models.SET_NULL, related_name="+")
            widget = models.ForeignKey(Parcel, on_delete=models.PROTECT, related_name="+")

            class Meta:
                app_label = "testapp"

        class Unmanaged(models.Model):
            buyer = models.ForeignKey(EmailUser, on_delete=models.PROTECT, related_name="+")

            class Meta:
                app_label = "testapp"
                managed = False

        class ReceiptProxy(Receipt):
            class Meta:
                app_label = "testapp"
                proxy = True

        monkeypatch.setattr(checks.apps, "get_models", lambda: [EmailUser, Receipt, Unmanaged, ReceiptProxy])

        assert account_problems() == [
            f"testapp.Receipt.{name} keys to the user model with {on_delete} - a person's rows there would block "
            "their deletion or outlive it. CASCADE, SET_NULL, or register testapp.Receipt with erasure()."
            for name, on_delete in (("buyer", "PROTECT"), ("seller", "DO_NOTHING"))
        ]
        erasure(Receipt, via="buyer", delete=True)
        assert account_problems() == []

    @isolate_apps("tests.testapp")
    def test_a_user_model_that_isnt_deletable(self, monkeypatch):
        class Plain(AbstractUser):
            class Meta:
                app_label = "testapp"

        monkeypatch.setattr(checks, "get_user_model", lambda: Plain)

        assert "testapp.Plain isn't a DeletableAccount - add it to the user model's bases." in account_problems()

    def test_each_is_an_error(self, settings):
        accounts(settings, COLOR="red")

        (found,) = accounts_can_work(None)
        assert (found.id, found.hint, found.msg) == (
            "isik.E029",
            "See isik.django.apps.accounts.",
            f"{AREA} has keys it doesn't know: COLOR.",
        )
