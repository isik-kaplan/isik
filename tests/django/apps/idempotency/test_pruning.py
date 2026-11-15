"""prune_claims() - claims spent longer ago than ISIK['DJANGO']['IDEMPOTENCY']['RETENTION'] deleted."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from isik.django.apps.idempotency.by_reference.models import IdempotencyClaim
from isik.django.apps.idempotency.claims import prune_claims
from isik.django.conf import override_isik
from tests.django.apps.idempotency.support import make_user


pytestmark = pytest.mark.django_db

CLAIMS = "idempotency_by_reference.IdempotencyClaim"


@pytest.fixture
def spent():
    """Claims spent three, two and one days ago, oldest first."""
    user = make_user("alice")
    now = timezone.now()
    return [
        IdempotencyClaim.objects.create(
            claimed_by=user, key=uuid.uuid4(), fingerprint="f", claimed_at=now - timedelta(days=days)
        )
        for days in (3, 2, 1)
    ]


def kept():
    return list(IdempotencyClaim.objects.order_by("claimed_at"))


def test_with_no_retention_keeps_every_claim(spent):
    assert prune_claims(CLAIMS) == 0
    assert kept() == spent


def test_deletes_those_older_than_retention(spent):
    with override_isik(DJANGO={"IDEMPOTENCY": {"RETENTION": timedelta(days=2, hours=1)}}):
        assert prune_claims(CLAIMS) == 1

    assert kept() == spent[1:]


def test_a_batch_at_a_time_the_oldest_first(spent):
    with override_isik(DJANGO={"IDEMPOTENCY": {"RETENTION": timedelta(hours=1)}}):
        assert prune_claims(CLAIMS, batch=2) == 2
        assert kept() == spent[2:]
        assert prune_claims(CLAIMS, batch=2) == 1

    assert kept() == []
