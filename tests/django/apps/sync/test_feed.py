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
