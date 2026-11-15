"""The sync log, as its triggers write it - every one installed as isik compiles it now."""

import pgtrigger
import pytest
from django.db import connection

from isik.django.apps.sync.bases import AbstractSyncLogEntry, SequenceField
from isik.django.apps.sync.choices import Operation
from isik.django.apps.sync.swapping import get_sync_log_model
from isik.django.apps.sync.triggers import MembershipTrigger, ParentLogTrigger, SyncLogTrigger
from tests.django.apps.sync import support
from tests.testapp.models import SyncBoard, SyncTeamMembership


pytestmark = pytest.mark.django_db

ALICE, BOB = "0190a000-0000-7000-8000-a00000000001", "0190a000-0000-7000-8000-a00000000002"
TEAM, OTHER_TEAM = "0190a000-0000-7000-8000-b00000000007", "0190a000-0000-7000-8000-b00000000008"
TASK = "0190a000-0000-7000-8000-100000000001"


def entries():
    """Every log entry, as (op, model, object_id, scope, old_scope, member), in order."""
    rows = get_sync_log_model().objects.order_by("xid", "seq")
    return [(row.op, row.model, row.object_id, row.scope, row.old_scope, row.member) for row in rows]


def transaction_id():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_current_xact_id()::text::bigint")
        return cursor.fetchone()[0]


def task(**changes):
    row = {"id": TASK, "owner": ALICE, "title": "t", "done": False, "project": None}
    return support.create("tasks", {**row, **changes})


@pytest.mark.parametrize(
    ("model", "trigger"),
    [
        (model, trigger)
        for model, trigger in pgtrigger.registered()
        if isinstance(trigger, SyncLogTrigger | MembershipTrigger | ParentLogTrigger)
        or issubclass(model, AbstractSyncLogEntry)
    ],
    ids=lambda each: getattr(each, "name", None) or each._meta.label,
)
def test_each_trigger_is_installed_as_compiled_now(model, trigger):
    assert trigger.get_installation_status(model) == ("INSTALLED", True)


class TestARow:
    def test_written_is_an_upsert_in_its_scope_by_its_transaction(self):
        task()

        assert entries() == [("upsert", "tasks", TASK, f"user:{ALICE}", None, None)]
        assert get_sync_log_model().objects.get().xid == transaction_id()

    def test_updated_is_an_upsert(self):
        task()
        support.update("tasks", TASK, {"title": "u"})

        assert entries()[1:] == [("upsert", "tasks", TASK, f"user:{ALICE}", None, None)]

    def test_saved_unchanged_writes_nothing(self):
        row = task()
        row.save()

        assert len(entries()) == 1

    def test_moved_says_the_scope_it_left(self):
        task()
        support.update("tasks", TASK, {"owner": BOB})

        assert entries()[1:] == [("upsert", "tasks", TASK, f"user:{BOB}", f"user:{ALICE}", None)]

    def test_deleted_is_a_delete_in_the_scope_it_was_in(self):
        task()
        support.delete("tasks", TASK)

        assert entries()[1:] == [("delete", "tasks", TASK, f"user:{ALICE}", None, None)]

    def test_entries_rise_in_order(self):
        task()
        support.update("tasks", TASK, {"title": "u"})

        first, second = get_sync_log_model().objects.order_by("seq")
        assert second.seq > first.seq
        assert str(first) == f"upsert tasks {TASK}"


class TestAMembership:
    def membership(self, **values):
        return SyncTeamMembership.objects.create(user=support.user(ALICE), team=support.team(TEAM), **values)

    def test_written_grants_its_scope(self):
        self.membership()

        assert entries() == [("scope_granted", None, None, f"team:{TEAM}", None, ALICE)]
        assert str(get_sync_log_model().objects.get()) == f"scope_granted team:{TEAM} {ALICE}"

    def test_written_not_meeting_the_condition_grants_nothing(self):
        self.membership(active=False)

        assert entries() == []

    def test_ceasing_to_meet_it_revokes_and_starting_again_grants(self):
        row = self.membership()
        row.active = False
        row.save()
        row.active = True
        row.save()

        assert [entry[0] for entry in entries()] == ["scope_granted", "scope_revoked", "scope_granted"]

    def test_saved_meeting_it_still_writes_nothing(self):
        row = self.membership()
        row.save()

        assert len(entries()) == 1

    def test_moved_to_another_scope_revokes_one_and_grants_the_other(self):
        row = self.membership()
        row.team = support.team(OTHER_TEAM)
        row.save()

        assert entries()[1:] == [
            ("scope_revoked", None, None, f"team:{TEAM}", None, ALICE),
            ("scope_granted", None, None, f"team:{OTHER_TEAM}", None, ALICE),
        ]

    def test_moved_to_another_person_revokes_and_grants(self):
        row = self.membership()
        row.user = support.user(BOB)
        row.save()

        assert [(entry[0], entry[5]) for entry in entries()[1:]] == [("scope_revoked", ALICE), ("scope_granted", BOB)]

    def test_deleted_revokes(self):
        self.membership().delete()

        assert [entry[0] for entry in entries()] == ["scope_granted", "scope_revoked"]

    def test_deleted_while_not_meeting_it_revokes_nothing(self):
        self.membership(active=False).delete()

        assert entries() == []


class TestACoveredRelation:
    def test_a_row_added_or_removed_writes_its_synced_row_again(self):
        board = SyncBoard.objects.create(owner=support.user(ALICE))
        team = support.team(TEAM)
        board.teams.add(team)
        board.teams.remove(team)

        assert entries() == [("upsert", "boards", str(board.pk), f"user:{ALICE}", None, None)] * 3


def test_the_sequence_is_numbered_by_the_database():
    assert SequenceField().db_type_suffix(connection) == "GENERATED BY DEFAULT AS IDENTITY"
    assert Operation.PRUNED == "pruned"
