"""The feed beyond the protocol's fixtures: cursors, limits, snapshots asked for, and rows a person can't see."""

import pytest
from django.db import connection
from django.test import override_settings
from rest_framework.permissions import BasePermission
from rest_framework.test import APIClient

from isik.django.apps.sync.feed import CursorExpired, decode_cursor, encode_cursor
from isik.django.apps.sync.swapping import get_sync_log_model
from isik.django.conf import override_isik
from tests.django.apps.sync import support
from tests.testapp.sync_views import TaskViewSet


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("routed")]

ALICE = "0190a000-0000-7000-8000-a00000000001"
TEAM = "0190a000-0000-7000-8000-b00000000007"
HEADERS = {"Isik-Sync-Protocol": "1"}


@pytest.fixture
def routed():
    with override_settings(ROOT_URLCONF="tests.testapp.sync_urls"):
        yield


@pytest.fixture
def client():
    made = APIClient()
    made.force_authenticate(support.user(ALICE))
    return made


def task(number, **values):
    return {"id": f"0190a000-0000-7000-8000-1000000000{number:02}", "owner": ALICE, "title": f"t{number}", **values}


def pulled(client, **query):
    response = client.get("/sync/changes", query, headers=HEADERS)
    assert response.status_code == 200, response.content
    return response.json()


def ops(page):
    return [(change["op"], change.get("model"), change.get("id") or change.get("scope")) for change in page["changes"]]


def caught_up(client):
    return pulled(client)["cursor"]


class TestACursor:
    def test_one_not_signed_here_is_expired(self, client):
        response = client.get("/sync/changes", {"cursor": "made-up"}, headers=HEADERS)

        assert (response.status_code, response.json()) == (410, {"code": "sync_cursor_expired"})
        with pytest.raises(CursorExpired):
            decode_cursor("made-up")

    def test_holds_its_state_signed(self):
        state = {"p": [1, 2], "open": None, "queue": []}

        assert decode_cursor(encode_cursor(state)) == state
        assert encode_cursor(state) == encode_cursor(state)


class TestTheLimit:
    @pytest.mark.parametrize("limit", ["0", "x", "-1"])
    def test_a_whole_number_of_at_least_one(self, client, limit):
        response = client.get("/sync/changes", {"limit": limit}, headers=HEADERS)

        assert (response.status_code, response.json()) == (400, {"limit": ["A whole number of at least 1."]})

    def test_page_limit_caps_what_a_client_asks(self, client):
        for number in range(3):
            support.create("tasks", task(number))
        with override_isik(DJANGO={"SYNC": {"PAGE_LIMIT": 2}}):
            asked = pulled(client, limit=5)
            unasked = pulled(client)

        assert [op for op, _, _ in ops(asked)].count("upsert") == 2
        assert asked["has_more"] is True
        assert [op for op, _, _ in ops(unasked)].count("upsert") == 2

    def test_page_limit_above_what_a_client_asks_leaves_it(self, client):
        for number in range(3):
            support.create("tasks", task(number))
        with override_isik(DJANGO={"SYNC": {"PAGE_LIMIT": 5}}):
            page = pulled(client, limit=1)

        assert [op for op, _, _ in ops(page)].count("upsert") == 1

    def test_unlimited_serves_everything(self, client):
        for number in range(3):
            support.create("tasks", task(number))
        cursor = caught_up(client)
        for number in range(3):
            support.update("tasks", task(number)["id"], {"title": "u"})

        page = pulled(client, cursor=cursor)
        assert len(page["changes"]) == 3
        assert page["has_more"] is False


class TestLiveChanges:
    def test_many_changes_to_one_row_past_a_page_collapse(self, client):
        support.create("tasks", task(1))
        cursor = caught_up(client)
        for title in "abcd":
            support.update("tasks", task(1)["id"], {"title": title})

        page = pulled(client, cursor=cursor, limit=1)
        assert ops(page) == [("upsert", "tasks", task(1)["id"])]
        assert page["changes"][0]["data"]["title"] == "d"
        assert page["has_more"] is False

    def test_an_entry_of_a_model_no_longer_synced_is_passed_over(self, client):
        cursor = caught_up(client)
        with connection.cursor() as sql:
            sql.execute(
                f"INSERT INTO {get_sync_log_model()._meta.db_table} (xid, op, model, object_id, scope) "
                "VALUES (pg_current_xact_id()::text::bigint, 'upsert', 'gone', '1', %s)",
                [f"user:{ALICE}"],
            )

        page = pulled(client, cursor=cursor, limit=1)
        assert ops(page) == []
        assert decode_cursor(page["cursor"])["p"] != decode_cursor(cursor)["p"]

    def test_the_watermark_is_read_once_a_page(self, client):
        support.create("tasks", task(1))

        assert pulled(client)["has_more"] is False


class TestSnapshotsAskedFor:
    def test_on_a_first_sync_follows_the_full_one(self, client):
        support.create("tasks", task(1))

        page = pulled(client, snapshot=["tasks", "nope"])
        assert ops(page) == [
            ("snapshot_begin", None, None),
            ("upsert", "tasks", task(1)["id"]),
            ("snapshot_end", None, None),
            ("snapshot_begin", "tasks", None),
            ("upsert", "tasks", task(1)["id"]),
            ("snapshot_end", "tasks", None),
        ]

    def test_asked_again_while_pending_or_open_is_one(self, client):
        for number in range(2):
            support.create("tasks", task(number))
        cursor = caught_up(client)

        first = pulled(client, cursor=cursor, snapshot=["tasks", "tasks"], limit=1)
        second = pulled(client, cursor=first["cursor"], snapshot="tasks", limit=1)
        assert [op for op, _, _ in ops(first) + ops(second)] == ["snapshot_begin", "upsert", "upsert", "snapshot_end"]
        assert decode_cursor(first["cursor"])["queue"] == []

    def test_asked_while_another_is_open_waits_for_it(self, client):
        for number in range(2):
            support.create("tasks", task(number))

        first = pulled(client, limit=1)
        second = pulled(client, cursor=first["cursor"], snapshot="projects")
        assert decode_cursor(first["cursor"])["open"]["model"] is None
        assert [op for op, model, _ in ops(second)] == ["upsert", "snapshot_end", "snapshot_begin", "snapshot_end"]

    def test_a_scope_snapshot_of_a_kind_no_model_holds_is_empty(self, client, monkeypatch):
        cursor = caught_up(client)
        state = decode_cursor(cursor)
        state["open"] = {"scope": "crew:1", "model": None, "after": None}
        monkeypatch.setattr("isik.django.apps.sync.feed.scopes_of", lambda user: [f"user:{ALICE}", "crew:1"])

        page = pulled(client, cursor=encode_cursor(state))
        assert ops(page) == [("snapshot_end", None, "crew:1")]


class NotTheSecondTask(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.title != "t2"


class NoOne(BasePermission):
    def has_permission(self, request, view):
        return False


class TestRowsAPersonCantSee:
    def test_hidden_by_an_object_permission_are_left_out_of_a_snapshot_and_deleted_live(self, client, monkeypatch):
        monkeypatch.setattr(TaskViewSet, "permission_classes", [NotTheSecondTask])
        for number in range(1, 4):
            support.create("tasks", task(number))

        first = pulled(client, limit=1)
        second = pulled(client, cursor=first["cursor"], limit=1)
        assert [change.get("id") for change in first["changes"] + second["changes"]][1:] == [
            task(1)["id"],
            task(3)["id"],
            None,
        ]
        support.update("tasks", task(2)["id"], {"done": True})
        assert ops(pulled(client, cursor=second["cursor"])) == [("delete", "tasks", task(2)["id"])]

    def test_hidden_rows_filling_a_read_are_read_past(self, client, monkeypatch):
        monkeypatch.setattr(TaskViewSet, "permission_classes", [NotTheSecondTask])
        for number in range(1, 4):
            support.create("tasks", task(number, title="t2" if number < 3 else "t3"))

        page = pulled(client, limit=1)
        assert ops(page) == [
            ("snapshot_begin", None, None),
            ("upsert", "tasks", task(3)["id"]),
            ("snapshot_end", None, None),
        ]

    def test_a_model_the_view_refuses_shows_nothing(self, client, monkeypatch):
        monkeypatch.setattr(TaskViewSet, "permission_classes", [NoOne])
        support.create("tasks", task(1))
        assert ops(pulled(client)) == [("snapshot_begin", None, None), ("snapshot_end", None, None)]

        cursor = caught_up(client)
        support.update("tasks", task(1)["id"], {"done": True})
        assert ops(pulled(client, cursor=cursor)) == [("delete", "tasks", task(1)["id"])]


BOB = "0190a000-0000-7000-8000-a00000000002"
OTHER_TEAM = "0190a000-0000-7000-8000-b00000000008"


@pytest.fixture
def other():
    opened = connection.copy()
    opened.ensure_connection()
    opened.set_autocommit(False)
    yield opened
    opened.rollback()
    opened.close()


def write_in(opened, number):
    with opened.cursor() as cursor:
        cursor.execute(
            "INSERT INTO testapp_synctask (id, owner_id, title, done, created_at, updated_at) "
            "VALUES (%s, %s, %s, false, now(), now())",
            [task(number)["id"], ALICE, f"t{number}"],
        )


class TestAgainstConcurrentTransactions:
    def test_a_change_committed_after_one_still_open_waits_for_it(self, client, other):
        cursor = caught_up(client)
        write_in(other, 1)
        support.create("tasks", task(2))

        held = pulled(client, cursor=cursor)
        assert (ops(held), held["has_more"], held["remaining"]) == ([], False, 0)
        other.commit()
        assert ops(pulled(client, cursor=held["cursor"])) == [
            ("upsert", "tasks", task(1)["id"]),
            ("upsert", "tasks", task(2)["id"]),
        ]

    def test_changes_come_by_transaction_then_by_when_they_were_made(self, client, other):
        cursor = caught_up(client)
        write_in(other, 1)
        support.create("tasks", task(2))
        write_in(other, 3)
        other.commit()

        assert [change["id"] for change in pulled(client, cursor=cursor, limit=2)["changes"]] == [
            task(1)["id"],
            task(3)["id"],
        ]


class TestScopes:
    def test_a_revoked_scope_and_a_change_after_it_both_come(self, client):
        support.grant(ALICE, f"team:{TEAM}")
        support.create("tasks", task(1))
        cursor = caught_up(client)
        support.revoke(ALICE, f"team:{TEAM}")
        support.update("tasks", task(1)["id"], {"done": True})

        assert ops(pulled(client, cursor=cursor)) == [
            ("scope_revoked", None, f"team:{TEAM}"),
            ("upsert", "tasks", task(1)["id"]),
        ]

    def test_another_person_s_grant_isn_t_theirs(self, client):
        cursor = caught_up(client)
        support.grant(BOB, f"team:{TEAM}")

        assert ops(pulled(client, cursor=cursor)) == []

    def test_a_scope_s_snapshot_holds_that_scope_s_rows_alone(self, client):
        support.grant(ALICE, f"team:{OTHER_TEAM}")
        support.create("notes", {"id": "0190a000-0000-7000-8000-300000000001", "team": OTHER_TEAM, "body": "x"})
        support.create("notes", {"id": "0190a000-0000-7000-8000-300000000002", "team": TEAM, "body": "y"})
        cursor = caught_up(client)
        support.grant(ALICE, f"team:{TEAM}")

        assert ops(pulled(client, cursor=cursor)) == [
            ("scope_granted", None, f"team:{TEAM}"),
            ("snapshot_begin", None, f"team:{TEAM}"),
            ("upsert", "notes", "0190a000-0000-7000-8000-300000000002"),
            ("snapshot_end", None, f"team:{TEAM}"),
        ]


class TestAPage:
    def test_a_limit_spans_one_snapshot_and_the_next(self, client):
        for number in range(1, 4):
            support.create("tasks", task(number))

        page = pulled(client, limit=4, snapshot="tasks")
        assert [op for op, _, _ in ops(page)] == [
            "snapshot_begin",
            "upsert",
            "upsert",
            "upsert",
            "snapshot_end",
            "snapshot_begin",
            "upsert",
        ]
        assert page["has_more"] is True

    def test_a_cursor_at_where_pruning_cut_is_still_good(self, client):
        support.create("tasks", task(1))
        cursor = caught_up(client)
        support.update("tasks", task(1)["id"], {"done": True})
        cursor = pulled(client, cursor=cursor)["cursor"]
        support.expire()

        assert ops(pulled(client, cursor=cursor)) == []

    def test_a_different_page_has_a_different_etag_and_isn_t_not_modified(self, client):
        cursor = caught_up(client)
        quiet = client.get("/sync/changes", {"cursor": cursor}, headers=HEADERS)
        support.create("tasks", task(1))
        changed = client.get("/sync/changes", {"cursor": cursor}, headers={**HEADERS, "If-None-Match": quiet["ETag"]})

        assert changed.status_code == 200
        assert changed["ETag"] != quiet["ETag"]

    def test_not_modified_says_its_etag(self, client):
        cursor = caught_up(client)
        quiet = client.get("/sync/changes", {"cursor": cursor}, headers=HEADERS)
        again = client.get("/sync/changes", {"cursor": cursor}, headers={**HEADERS, "If-None-Match": quiet["ETag"]})

        assert (again.status_code, again["ETag"]) == (304, quiet["ETag"])

    def test_a_row_is_shown_as_retrieve_shows_it(self, client, monkeypatch):
        from rest_framework import serializers

        from tests.testapp.models import SyncTask

        class Titles(serializers.ModelSerializer):
            class Meta:
                model = SyncTask
                fields = ["id", "title"]

        monkeypatch.setattr(TaskViewSet, "serializer_class_action_map", {"retrieve": Titles})
        support.create("tasks", task(1))

        assert pulled(client)["changes"][1]["data"] == {"id": task(1)["id"], "title": "t1"}

    def test_another_person_s_tasks_aren_t_read(self, client):
        support.create("tasks", task(1, owner=BOB))

        assert ops(pulled(client)) == [("snapshot_begin", None, None), ("snapshot_end", None, None)]


class TestProgress:
    def test_remaining_is_never_below_what_comes_and_none_at_the_end(self, client):
        for number in range(1, 4):
            support.create("tasks", task(number))
        cursor = caught_up(client)
        for title in "abc":
            support.update("tasks", task(1)["id"], {"title": title})
            support.update("tasks", task(2)["id"], {"title": title})
        support.create("tasks", task(4, owner=BOB))

        page = {"cursor": cursor, "has_more": True}
        pages = []
        while page["has_more"]:
            page = pulled(client, limit=1, cursor=page["cursor"])
            pages.append((len(page["changes"]), page["remaining"]))

        # Three entries for each of two rows, interleaved, and one of a scope the person isn't in - fewer row
        # changes come after each page than it counts, since a page collapses a row's entries.
        assert pages[0] == (1, 5)
        for at, (_, remaining) in enumerate(pages):
            assert remaining >= sum(count for count, _ in pages[at + 1 :])
        assert pages[-1][1] == 0

    def test_remaining_counts_each_entry_a_page_collapses(self, client):
        support.create("tasks", task(1))
        support.create("tasks", task(2))
        cursor = caught_up(client)
        support.update("tasks", task(1)["id"], {"title": "a"})
        for title in "abc":
            support.update("tasks", task(2)["id"], {"title": title})

        first = pulled(client, cursor=cursor, limit=1)
        last = pulled(client, cursor=first["cursor"], limit=1)

        assert (len(first["changes"]), first["remaining"]) == (1, 3)
        assert (len(last["changes"]), last["remaining"], last["has_more"]) == (1, 0, False)

    def test_remaining_counts_what_an_open_snapshot_holds_back(self, client):
        for number in range(1, 3):
            support.create("tasks", task(number))
        first = pulled(client, limit=1)
        support.update("tasks", task(1)["id"], {"done": True})
        second = pulled(client, cursor=first["cursor"], limit=1)
        third = pulled(client, cursor=second["cursor"])

        assert (first["has_more"], first["remaining"]) == (True, 0)
        assert (second["has_more"], second["remaining"]) == (True, 1)
        assert (third["has_more"], third["remaining"]) == (False, 0)

    def test_a_snapshot_says_how_many_rows_it_serves_once(self, client):
        for number in range(1, 4):
            support.create("tasks", task(number))
        support.create("projects", {"id": "0190a000-0000-7000-8000-200000000001", "owner": ALICE, "name": "p"})

        first = pulled(client, limit=1)
        support.create("tasks", task(5))
        rest = pulled(client, cursor=first["cursor"])

        assert first["changes"][0] == {"op": "snapshot_begin", "scope": None, "model": None, "total": 4}
        assert all(change["op"] != "snapshot_begin" for change in rest["changes"])

    def test_a_snapshot_s_total_leaves_out_what_the_view_refuses(self, client, monkeypatch):
        monkeypatch.setattr(TaskViewSet, "permission_classes", [NoOne])
        support.create("tasks", task(1))

        assert pulled(client)["changes"][0]["total"] == 0

    def test_a_snapshot_across_models_goes_on_from_each_model_s_first_row(self, client):
        support.create("projects", {"id": "0190a000-0000-7000-8000-900000000001", "owner": ALICE, "name": "p"})
        support.create("tasks", task(1))

        first = pulled(client, limit=1)
        second = pulled(client, cursor=first["cursor"], limit=1)

        assert ops(second) == [("upsert", "tasks", task(1)["id"]), ("snapshot_end", None, None)]


def test_a_cursor_made_a_second_later_is_the_same(monkeypatch):
    state = {"p": [1, 2], "queue": []}
    first = encode_cursor(state)
    monkeypatch.setattr("time.time", lambda: 10**10)

    assert encode_cursor(state) == first
    assert decode_cursor(first) == state


@pytest.mark.usefixtures("routed")
def test_a_page_full_after_a_revoked_scope_goes_on_past_it(client):
    support.grant(ALICE, f"team:{TEAM}")
    support.create("tasks", task(1))
    cursor = caught_up(client)
    support.update("tasks", task(1)["id"], {"done": True})
    support.revoke(ALICE, f"team:{TEAM}")
    support.create("tasks", task(2))

    first = pulled(client, cursor=cursor, limit=1)
    second = pulled(client, cursor=first["cursor"], limit=1)

    assert ops(first) == [("upsert", "tasks", task(1)["id"]), ("scope_revoked", None, f"team:{TEAM}")]
    assert ops(second) == [("upsert", "tasks", task(2)["id"])]


@pytest.mark.usefixtures("routed")
class TestTheCursor:
    def test_two_snapshots_asked_for_the_first_spanning_pages_keeps_the_second(self, client):
        for number in range(1, 3):
            support.create("tasks", task(number))
        support.create("projects", {"id": "0190a000-0000-7000-8000-200000000001", "owner": ALICE, "name": "p"})
        cursor = caught_up(client)

        first = pulled(client, cursor=cursor, snapshot=["tasks", "projects"], limit=1)
        rest = pulled(client, cursor=first["cursor"])

        assert decode_cursor(first["cursor"])["queue"] == ["projects"]
        assert [(op, model) for op, model, _ in ops(rest)] == [
            ("upsert", "tasks"),
            ("snapshot_end", "tasks"),
            ("snapshot_begin", "projects"),
            ("upsert", "projects"),
            ("snapshot_end", "projects"),
        ]

    def test_a_snapshot_revoked_while_served_is_closed_in_it(self, client):
        support.create("notes", {"id": "0190a000-0000-7000-8000-300000000001", "team": TEAM, "body": "x"})
        support.create("notes", {"id": "0190a000-0000-7000-8000-300000000002", "team": TEAM, "body": "y"})
        cursor = caught_up(client)
        support.grant(ALICE, f"team:{TEAM}")
        first = pulled(client, cursor=cursor, limit=1)
        support.revoke(ALICE, f"team:{TEAM}")

        assert decode_cursor(pulled(client, cursor=first["cursor"])["cursor"])["open"] is None

    def test_at_the_end_it_moves_past_the_changes_of_scopes_the_person_isn_t_in(self, client):
        cursor = caught_up(client)
        support.create("tasks", task(1, owner=BOB))

        moved = pulled(client, cursor=cursor)
        assert ops(moved) == []
        assert decode_cursor(moved["cursor"])["p"] > decode_cursor(cursor)["p"]
