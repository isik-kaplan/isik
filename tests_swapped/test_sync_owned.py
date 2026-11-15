"""The sync log the project's own - its triggers, pruning and feed reading the project's table, never isik's."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from isik.django.apps.sync.log import horizon, prune_sync_log
from isik.django.apps.sync.swapping import get_sync_log_model
from isik.django.conf import isik_areas
from tests_swapped.owned import models as owned


pytestmark = pytest.mark.django_db


def test_the_getter_is_the_project_s_log_and_isik_s_has_no_table():
    assert get_sync_log_model() is owned.SyncLogEntry
    assert "isik_sync_log" not in connection.introspection.table_names()


def test_a_synced_row_and_a_membership_are_logged_in_the_project_s_table(settings):
    organization = owned.Organization.objects.create(name="acme")
    alice = get_user_model().objects.create(username="alice", email="alice@example.com")
    owned.Membership.objects.create(organization=organization, user=alice)
    folder = owned.Folder.objects.create(organization=organization, name="f")

    assert list(owned.SyncLogEntry.objects.order_by("seq").values_list("op", "scope", "object_id", "member")) == [
        ("scope_granted", f"organization:{organization.pk}", None, str(alice.pk)),
        ("upsert", f"organization:{organization.pk}", str(folder.pk), None),
    ]


@pytest.mark.django_db(transaction=True)
def test_pruning_cuts_the_project_s_table(settings):
    organization = owned.Organization.objects.create(name="acme")
    owned.Folder.objects.create(organization=organization, name="f")
    isik_areas(settings).SYNC = {"LOG_MODEL": "owned.SyncLogEntry", "RETENTION": timedelta(microseconds=1)}

    assert prune_sync_log() == 1
    assert horizon() is not None
    assert list(owned.SyncLogEntry.objects.values_list("op", flat=True)) == ["pruned"]
