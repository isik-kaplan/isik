"""The claim models, which one is used, how a key is claimed, and how a kept body is written."""

import datetime
import decimal
import uuid

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connection, models
from django.test import override_settings
from rest_framework.response import Response

from isik.django.apps.idempotency import claims
from isik.django.apps.idempotency.by_reference.models import IdempotencyClaim
from isik.django.apps.idempotency.claims import (
    JSONBodyCodec,
    claim_idempotency_key,
    configured_body_codec,
    get_claim_model,
)
from isik.django.apps.idempotency.exceptions import IdempotencyKeyInFlight
from isik.django.apps.idempotency.with_body.models import IdempotencyClaimWithBody
from tests.django.apps.idempotency.support import make_user


class ReversingCodec:
    def encode(self, data):
        return JSONBodyCodec().encode(data)[::-1]

    def decode(self, text):
        return JSONBodyCodec().decode(text[::-1])


reversing_codec = ReversingCodec()


def lock_timeout():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('lock_timeout')")
        return cursor.fetchone()[0]


class TestGetClaimModel:
    def test_a_model_passed_in_is_used_as_is(self):
        assert get_claim_model(IdempotencyClaimWithBody) is IdempotencyClaimWithBody

    def test_a_label_passed_in_is_looked_up(self):
        assert get_claim_model("idempotency_with_body.IdempotencyClaimWithBody") is IdempotencyClaimWithBody

    @override_settings(IDEMPOTENCY_CLAIM_MODEL="idempotency_with_body.IdempotencyClaimWithBody")
    def test_the_setting_names_the_model_otherwise(self):
        assert get_claim_model() is IdempotencyClaimWithBody

    @override_settings(IDEMPOTENCY_CLAIM_MODEL="idempotency_with_body.IdempotencyClaimWithBody")
    def test_a_model_passed_in_wins_over_the_setting(self):
        assert get_claim_model(IdempotencyClaim) is IdempotencyClaim

    @pytest.mark.parametrize(
        ("installed", "expected"),
        [
            ("isik.django.apps.idempotency.by_reference", IdempotencyClaim),
            ("isik.django.apps.idempotency.with_body", IdempotencyClaimWithBody),
        ],
    )
    def test_with_neither_the_one_installed_claim_app_decides(self, monkeypatch, installed, expected):
        monkeypatch.setattr(apps, "is_installed", lambda app: app == installed)

        assert get_claim_model() is expected

    @pytest.mark.parametrize("installed", [set(), set(claims.CLAIM_APPS)], ids=["neither", "both"])
    def test_none_or_both_installed_is_a_misconfiguration(self, monkeypatch, installed):
        monkeypatch.setattr(apps, "is_installed", lambda app: app in installed)

        with pytest.raises(ImproperlyConfigured) as raised:
            get_claim_model()

        assert str(raised.value) == (
            "Install exactly one of isik.django.apps.idempotency.by_reference and "
            "isik.django.apps.idempotency.with_body, or set IDEMPOTENCY_CLAIM_MODEL to the claim model to use."
        )


@pytest.mark.django_db
class TestClaimIdempotencyKey:
    def test_the_first_claim_is_created(self):
        alice, key = make_user("alice"), uuid.uuid4()

        claim, created = claim_idempotency_key(IdempotencyClaim, alice, key, "f" * 64)

        assert created is True
        stored = IdempotencyClaim.objects.get()
        assert (stored.pk, stored.claimed_by, stored.key, stored.fingerprint, stored.status) == (
            claim.pk,
            alice,
            key,
            "f" * 64,
            None,
        )

    def test_a_committed_claim_is_returned_rather_than_made_twice(self):
        alice, key = make_user("alice"), uuid.uuid4()
        first, _ = claim_idempotency_key(IdempotencyClaim, alice, key, "a" * 64)

        again, created = claim_idempotency_key(IdempotencyClaim, alice, key, "b" * 64)

        assert (again.pk, again.fingerprint, created) == (first.pk, "a" * 64, False)
        assert IdempotencyClaim.objects.count() == 1

    def test_the_claim_returned_is_this_callers_for_this_key(self):
        alice, bob, key = make_user("alice"), make_user("bob"), uuid.uuid4()
        mine, _ = claim_idempotency_key(IdempotencyClaim, alice, key, "a" * 64)
        claim_idempotency_key(IdempotencyClaim, bob, key, "b" * 64)
        claim_idempotency_key(IdempotencyClaim, alice, uuid.uuid4(), "c" * 64)

        again, created = claim_idempotency_key(IdempotencyClaim, alice, key, "a" * 64)

        assert (again.pk, created) == (mine.pk, False)

    def test_two_callers_never_share_a_key(self):
        key = uuid.uuid4()
        claim_idempotency_key(IdempotencyClaim, make_user("alice"), key, "a" * 64)

        _, created = claim_idempotency_key(IdempotencyClaim, make_user("bob"), key, "a" * 64)

        assert created is True

    def test_a_lock_timeout_lasts_only_as_long_as_the_insert(self):
        before = lock_timeout()

        claim_idempotency_key(IdempotencyClaim, make_user("alice"), uuid.uuid4(), "a" * 64, lock_timeout=250)

        assert lock_timeout() == before

    def test_a_lock_timeout_is_applied_to_the_insert(self, monkeypatch):
        alice = make_user("alice")
        seen = []
        original = models.QuerySet.create

        def create(queryset, **kwargs):
            seen.append(lock_timeout())
            return original(queryset, **kwargs)

        monkeypatch.setattr(models.QuerySet, "create", create)

        claim_idempotency_key(IdempotencyClaim, alice, uuid.uuid4(), "a" * 64, lock_timeout=250)

        assert seen == ["250ms"]

    def test_running_out_of_lock_timeout_is_in_flight(self, monkeypatch):
        alice = make_user("alice")

        class LockNotAvailable(Exception):
            sqlstate = "55P03"

        def create(queryset, **kwargs):
            raise OperationalError("canceling statement due to lock timeout") from LockNotAvailable()

        monkeypatch.setattr(models.QuerySet, "create", create)

        with pytest.raises(IdempotencyKeyInFlight):
            claim_idempotency_key(IdempotencyClaim, alice, uuid.uuid4(), "a" * 64, lock_timeout=250)

    def test_any_other_operational_error_is_left_alone(self, monkeypatch):
        alice = make_user("alice")
        failure = OperationalError("the server went away")

        def create(queryset, **kwargs):
            raise failure

        monkeypatch.setattr(models.QuerySet, "create", create)

        with pytest.raises(OperationalError) as raised:
            claim_idempotency_key(IdempotencyClaim, alice, uuid.uuid4(), "a" * 64)

        assert raised.value is failure


@pytest.mark.django_db
def test_recording_keeps_the_response_by_default():
    claim, _ = claim_idempotency_key(IdempotencyClaimWithBody, make_user("alice"), uuid.uuid4(), "a" * 64)

    claim.record(view=None, response=Response({"made": 1}, status=201))

    claim.refresh_from_db()
    assert (claim.status, claim.body) == (201, '{"made": 1}')


class TestBodyCodecs:
    def test_json_is_written_with_drfs_encoder(self):
        # What a serializer hands a Response is already plain - the encoder covers what a handler's own
        # dict might still hold.
        data = {"when": datetime.date(2026, 10, 2), "price": decimal.Decimal("1.50"), "id": uuid.UUID(int=1)}

        assert JSONBodyCodec().encode(data) == (
            '{"when": "2026-10-02", "price": 1.5, "id": "00000000-0000-0000-0000-000000000001"}'
        )

    def test_json_reads_back_what_it_wrote(self):
        assert JSONBodyCodec().decode('{"a": [1, null]}') == {"a": [1, None]}

    def test_json_is_the_default_codec(self):
        assert isinstance(configured_body_codec(), JSONBodyCodec)

    @override_settings(IDEMPOTENCY_BODY_CODEC=reversing_codec)
    def test_the_setting_takes_a_codec(self):
        assert configured_body_codec() is reversing_codec

    @override_settings(IDEMPOTENCY_BODY_CODEC=ReversingCodec)
    def test_the_setting_takes_a_codec_class_and_makes_one(self):
        assert isinstance(configured_body_codec(), ReversingCodec)

    @override_settings(IDEMPOTENCY_BODY_CODEC="tests.django.apps.idempotency.test_claims.reversing_codec")
    def test_the_setting_takes_a_dotted_path_to_a_codec(self):
        assert configured_body_codec() is reversing_codec

    @override_settings(IDEMPOTENCY_BODY_CODEC="tests.django.apps.idempotency.test_claims.ReversingCodec")
    def test_the_setting_takes_a_dotted_path_to_a_codec_class(self):
        assert isinstance(configured_body_codec(), ReversingCodec)

    @override_settings(IDEMPOTENCY_BODY_CODEC=reversing_codec)
    def test_a_claim_uses_the_configured_codec(self):
        assert IdempotencyClaimWithBody().get_body_codec() is reversing_codec

    @override_settings(IDEMPOTENCY_BODY_CODEC=reversing_codec)
    def test_a_claims_own_codec_wins_over_the_setting(self, monkeypatch):
        own = JSONBodyCodec()
        monkeypatch.setattr(IdempotencyClaimWithBody, "body_codec", own)

        assert IdempotencyClaimWithBody().get_body_codec() is own


def test_the_two_claims_differ_only_in_what_they_keep():
    def columns(model):
        return {field.name for field in model._meta.concrete_fields}

    shared = {"id", "claimed_by", "key", "fingerprint", "status", "claimed_at"}
    assert columns(IdempotencyClaim) == shared | {"object_id"}
    assert columns(IdempotencyClaimWithBody) == shared | {"body"}


def test_a_key_is_unique_per_caller():
    (constraint,) = IdempotencyClaim._meta.constraints

    assert (constraint.fields, constraint.name) == (
        ("claimed_by", "key"),
        "idempotency_by_reference_idempotencyclaim_key",
    )
