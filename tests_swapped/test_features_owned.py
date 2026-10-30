"""
The features app with an organization as the holder and every model the project's own - the shape a
multi-tenant project takes: grants, overrides and the boundary sweep in its tables, one setting's own value a
field of the organization itself.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import ProgrammingError, connection, transaction
from django.db.models import Prefetch
from django.utils import timezone

from isik.django.apps.features import swapping
from isik.django.apps.features.checks import declaration_problems, setting_problems
from isik.django.apps.features.operations import (
    force_setting,
    grant,
    release_setting,
    revoke,
    set_setting,
    sweep_boundaries,
)
from isik.django.apps.features.resolution import effective, effective_expression, forget, has_feature
from isik.django.apps.notifications.pipeline import fan_out
from tests_swapped.owned import models as owned


pytestmark = pytest.mark.django_db

REASON = "the organization is over its seats while its invoice is disputed"

GETTERS = {
    swapping.get_holder_model: owned.Organization,
    swapping.get_feature_model: owned.Feature,
    swapping.get_grant_model: owned.FeatureGrant,
    swapping.get_sweep_model: owned.SweepCursor,
    swapping.get_overrides_model: owned.OrganizationOverrides,
}


@pytest.fixture
def acme():
    return owned.Organization.objects.create(name="Acme", seat_limit=25)


def test_every_getter_gives_the_projects_model():
    assert {getter: getter() for getter in GETTERS} == GETTERS


def test_isiks_own_tables_were_never_made():
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE tablename LIKE 'isik_features%'")
        tables = cursor.fetchall()
        cursor.execute("SELECT proname FROM pg_proc WHERE prosrc LIKE '%isik_features%'")
        functions = cursor.fetchall()
    assert (tables, functions) == ([], [])


def test_the_checks_have_nothing_to_say():
    assert (setting_problems(), declaration_problems()) == ([], [])


def test_the_declared_features_are_mirrored_into_the_projects_catalog():
    assert sorted(owned.Feature.objects.values_list("name", flat=True)) == ["large_teams", "webhooks"]


def test_a_grant_is_kept_in_the_projects_ledger(acme):
    grant(acme, "webhooks", source="subscription", reference="inv-1")

    assert has_feature(acme, "webhooks")
    assert owned.FeatureGrant.objects.get().holder == acme
    with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot delete"):
        owned.FeatureGrant.objects.all().delete()
    with transaction.atomic(), pytest.raises(ProgrammingError, match="Cannot update"):
        owned.FeatureGrant.objects.update(source="manual")


def test_a_grant_reaches_the_organizations_members(acme):
    alice = get_user_model().objects.create(username="alice", email="alice@example.com")
    get_user_model().objects.create(username="mallory", email="mallory@example.com")
    owned.Membership.objects.create(organization=acme, user=alice)

    grant(acme, "webhooks", source="manual")
    fan_out()

    assert list(owned.InboxDelivery.objects.values_list("user_id", flat=True)) == [str(alice.pk)]


def test_a_setting_kept_on_the_organization_itself(acme):
    # Its own 25, clamped to 10 without the feature; with it, 25.
    assert (effective(acme, "seat_limit").value, effective(acme, "seat_limit").origin) == (10, "limited")

    grant(acme, "large_teams", source="manual")

    assert (effective(acme, "seat_limit").value, effective(acme, "seat_limit").origin) == (25, "own")

    set_setting(acme, "seat_limit", 40)
    acme.refresh_from_db()
    assert acme.seat_limit == 40
    assert not owned.OrganizationOverrides.objects.exists()


def test_a_force_on_it_keeps_the_organizations_own_value(acme):
    grant(acme, "large_teams", source="manual")

    force_setting(acme, "seat_limit", 3, source="staff", reason=REASON)

    assert (effective(acme, "seat_limit").value, effective(acme, "seat_limit").origin) == (3, "forced")
    acme.refresh_from_db()
    assert acme.seat_limit == 25
    assert owned.OrganizationOverrides.pgh_event_model.objects.filter(seat_limit_forced=True).exists()

    release_setting(acme, "seat_limit")

    assert effective(acme, "seat_limit").value == 25


def test_the_same_answer_in_sql(acme):
    other = owned.Organization.objects.create(name="Other", seat_limit=8)
    grant(acme, "large_teams", source="manual")
    force_setting(other, "max_endpoints", 4, source="staff", reason=REASON)

    seats = dict(
        owned.Organization.objects.annotate(seats=effective_expression("seat_limit")).values_list("name", "seats")
    )
    endpoints = dict(
        owned.Organization.objects.annotate(endpoints=effective_expression("max_endpoints")).values_list(
            "name", "endpoints"
        )
    )

    assert (seats, endpoints) == ({"Acme": 25, "Other": 8}, {"Acme": 0, "Other": 4})


def test_the_sweep_keeps_its_place_in_the_projects_table(acme):
    now = timezone.now()
    sweep_boundaries(now)
    grant(acme, "webhooks", source="manual", starts_at=now - timedelta(hours=1), expires_at=now + timedelta(hours=1))
    forget(acme)

    assert sweep_boundaries(now + timedelta(hours=2)) == 1
    assert owned.SweepCursor.objects.get().swept_at == now + timedelta(hours=2)


def test_erasing_the_organization(acme):
    grant(acme, "webhooks", source="manual")
    force_setting(acme, "max_endpoints", 2, source="staff", reason=REASON)
    revoke(acme, "webhooks")

    acme.delete()

    assert not owned.FeatureGrant.objects.exists()
    assert not owned.OrganizationOverrides.pgh_event_model.objects.exists()


def test_grants_loaded_with_the_organizations_are_used(monkeypatch, django_assert_num_queries):
    now = timezone.now()
    held = {
        "starts now": dict(starts_at=now),
        "ends later": dict(starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=1)),
        "starts later": dict(starts_at=now + timedelta(days=1)),
        "ended now": dict(starts_at=now - timedelta(days=1), expires_at=now),
        "revoked now": dict(starts_at=now - timedelta(days=1), revoked_at=now),
        "revoked later": dict(starts_at=now - timedelta(days=1), revoked_at=now + timedelta(days=1)),
    }
    for name, terms in held.items():
        owned.FeatureGrant.objects.create(
            holder=owned.Organization.objects.create(name=name),
            feature=owned.Feature.objects.get(name="webhooks"),
            source="manual",
            **terms,
        )
    owned.Organization.objects.create(name="none")
    monkeypatch.setattr(timezone, "now", lambda: now)

    with django_assert_num_queries(2):
        loaded = owned.Organization.objects.prefetch_related(
            Prefetch("feature_grants", queryset=owned.FeatureGrant.objects.select_related("feature"))
        )
        holding = {organization.name: has_feature(organization, "webhooks") for organization in loaded}

    assert holding == {
        "starts now": True,
        "ends later": True,
        "starts later": False,
        "ended now": False,
        "revoked now": False,
        "revoked later": True,
        "none": False,
    }


def test_a_write_forgets_the_grants_loaded_with_the_organization(acme):
    (loaded,) = owned.Organization.objects.prefetch_related("feature_grants")
    assert has_feature(loaded, "webhooks") is False

    grant(loaded, "webhooks", source="manual")

    assert has_feature(loaded, "webhooks") is True
    assert "feature_grants" not in loaded._prefetched_objects_cache
