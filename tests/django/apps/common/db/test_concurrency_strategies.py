"""The three ways a version covers more than its token: computed in SQL, a column its relations move, and
the model's own concurrency_parts()."""

import datetime
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from django.db import connection, connections, models, transaction
from django.test.utils import isolate_apps

from isik.common.utils.exemptions import declared_exemptions
from isik.django.apps.common.db import models as base_models
from isik.django.apps.common.db.concurrency import (
    ConcurrencyParts,
    ConcurrencyToken,
    assert_concurrency_field_changes,
    concurrency_etag,
    concurrency_strategy,
    concurrency_token_problems,
    current_concurrency_etag,
    with_concurrency_etags,
)
from tests.testapp.models import EmailUser, Roster, Squad, SquadPost, Tag, Team, TeamNote, Widget


# Where this module's code says it is - see tests/test_reasons.py.
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())
REASON = "its tags are read by concurrency_parts(), and nothing else counts"
TOKEN = "moved on by one by a trigger of the project's own on every write"


@pytest.fixture
def alice():
    return EmailUser.objects.create(username="alice", email="alice@example.com")


@pytest.fixture
def tag():
    return Tag.objects.create(label="red")


def isolated(name, **attributes):
    @isolate_apps("tests.testapp")
    def define():
        meta = type("Meta", (), {"app_label": "testapp"})
        # A token of its own, so the strategy's problems are the ones left.
        token = {"revision": models.IntegerField(), "concurrency_field": ConcurrencyToken("revision", reason=TOKEN)}
        return type(name, (models.Model,), {"__module__": __name__, "Meta": meta, **token, **attributes})

    return define()


class TestWhichStrategy:
    def test_computed_once_relations_are_named(self):
        assert concurrency_strategy(Team) == "computed"

    def test_the_one_declared(self):
        assert concurrency_strategy(Squad) == "column"
        assert concurrency_strategy(Roster) == "parts"

    def test_none_for_a_token_alone(self):
        assert concurrency_strategy(Widget) is None

    @pytest.mark.parametrize("model", [Team, Squad, Roster])
    def test_each_declared_well_has_no_problem(self, model):
        assert concurrency_token_problems(model) == []

    def test_one_it_doesnt_know_is_a_problem(self):
        model = isolated("OddStrategy", concurrency_strategy="sometimes")

        assert (
            "testapp.OddStrategy's concurrency_strategy is 'sometimes' - it takes 'computed', 'column' or "
            "ConcurrencyParts(reason=...)."
        ) in concurrency_token_problems(model)

    @pytest.mark.parametrize("strategy", ["computed", "column"])
    def test_one_covering_relations_must_name_some(self, strategy):
        model = isolated(f"Bare{strategy.title()}", concurrency_strategy=strategy)

        assert (
            f"testapp.Bare{strategy.title()}'s concurrency_strategy {strategy!r} covers relations, and it names no "
            "concurrency_relations."
        ) in concurrency_token_problems(model)

    def test_parts_must_be_read_by_a_method(self):
        model = isolated("Partless", concurrency_strategy=ConcurrencyParts(reason=REASON))

        assert (
            "testapp.Partless's concurrency_strategy is ConcurrencyParts, and it has no concurrency_parts() method."
        ) in concurrency_token_problems(model)


@pytest.mark.django_db
class TestColumn:
    def test_a_relation_change_moves_the_version_by_one(self, alice):
        squad = Squad.objects.create(name="a")

        squad.members.add(alice)
        squad.refresh_from_db()
        assert squad.version == 2

        squad.members.remove(alice)
        squad.refresh_from_db()
        assert squad.version == 3

    def test_moves_updated_at_too(self, alice):
        squad = Squad.objects.create(name="a")
        squad.refresh_from_db()
        stamped = squad.updated_at

        squad.members.add(alice)
        squad.refresh_from_db()

        assert squad.updated_at > stamped

    def test_a_row_pointing_at_it_moves_the_version_too(self):
        squad = Squad.objects.create(name="a")
        post = SquadPost.objects.create(squad=squad, text="hi")
        post.delete()

        squad.refresh_from_db()
        assert squad.version == 3

    def test_the_etag_is_the_version_as_ever(self, alice):
        squad = Squad.objects.create(name="a")
        squad.members.add(alice)

        assert current_concurrency_etag(Squad, squad.pk) == '"2"'
        assert concurrency_etag(Squad.objects.get(pk=squad.pk)) == '"2"'

    def test_the_same_rows_back_are_a_new_version(self, alice):
        squad = Squad.objects.create(name="a")
        squad.members.add(alice)
        squad.members.remove(alice)

        assert current_concurrency_etag(Squad, squad.pk) == '"3"'

    def test_a_list_reads_it_from_the_row(self):
        queryset = Squad.objects.all()

        assert with_concurrency_etags(queryset) is queryset

    def test_its_trigger_moves_the_owners_version_on(self):
        trigger = base_models._relation_lock_trigger(Squad, "members", Squad.members.through._meta.get_field("squad"))

        assert trigger.name == "bump_members_owner"
        assert trigger.func == (
            'IF TG_OP <> \'INSERT\' THEN UPDATE "testapp_squad" SET "version" = "version" + 1 WHERE "id" = '
            'OLD."squad_id"; END IF; IF TG_OP <> \'DELETE\' THEN UPDATE "testapp_squad" SET "version" = '
            '"version" + 1 WHERE "id" = NEW."squad_id"; END IF; RETURN NULL;'
        )

    def test_the_helper_proves_it(self, alice):
        assert_concurrency_field_changes(
            Squad.objects.create(name="a"),
            related={"members": alice, "posts": lambda squad: SquadPost.objects.create(squad=squad, text="hi")},
        )


@pytest.mark.django_db
class TestParts:
    def test_the_reason_is_a_listed_exemption(self):
        made, line = ConcurrencyParts(reason=REASON), sys._getframe().f_lineno

        assert declared_exemptions("isik.concurrency-parts.unproven")[-1] is made
        assert (made.file, made.line) == (HERE, line)

    def test_the_etag_hashes_the_token_and_the_parts(self, tag):
        roster = Roster.objects.create(name="a")
        roster.tags.add(tag)
        roster.refresh_from_db()
        token = concurrency_etag(Widget(updated_at=roster.updated_at)).strip('"')
        text = json.dumps([token, ["red"]], sort_keys=True, default=str, separators=(",", ":"))

        assert concurrency_etag(roster) == f'"{hashlib.sha256(text.encode()).hexdigest()}"'
        assert current_concurrency_etag(Roster, roster.pk) == concurrency_etag(roster)

    def test_the_parts_read_now_count(self, tag):
        roster = Roster.objects.create(name="a")
        before = concurrency_etag(roster)
        roster.tags.add(tag)

        assert concurrency_etag(roster) != before

    def test_a_part_that_isnt_json_reads_as_its_text(self, monkeypatch):
        moment = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        monkeypatch.setattr(Roster, "concurrency_parts", lambda self: [moment])
        text = json.dumps(["1767225600000000", ["2026-01-01 00:00:00+00:00"]], sort_keys=True, separators=(",", ":"))

        assert concurrency_etag(Roster(name="a", updated_at=moment)) == f'"{hashlib.sha256(text.encode()).hexdigest()}"'

    def test_a_mapping_among_the_parts_reads_the_same_in_any_order(self, monkeypatch):
        moment = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        monkeypatch.setattr(Roster, "concurrency_parts", lambda self: [{"b": 1, "a": 2}])
        text = '["1767225600000000",[{"a":2,"b":1}]]'

        assert concurrency_etag(Roster(name="a", updated_at=moment)) == f'"{hashlib.sha256(text.encode()).hexdigest()}"'

    def test_a_row_that_isnt_there_has_none(self):
        assert current_concurrency_etag(Roster, Roster(name="x").pk) is None

    def test_a_list_reads_it_from_each_row(self):
        queryset = Roster.objects.all()

        assert with_concurrency_etags(queryset) is queryset

    def test_its_named_relations_get_the_lock_trigger(self):
        locks = {(owner, relation): trigger for owner, relation, _proxy, trigger in base_models.relation_locks}

        assert locks[Roster, "tags"].name == "lock_tags_owner"

    def test_the_helper_proves_it(self, tag):
        assert_concurrency_field_changes(Roster.objects.create(name="a"), related={"tags": tag})


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("model", "relation"), [(Team, "members"), (Squad, "members"), (Roster, "tags"), (Team, "notes"), (Squad, "posts")]
)
def test_removing_a_related_row_waits_for_a_write_holding_the_owner(model, relation):
    owner = model.objects.create(name="core")
    # A row pointing at the owner, by the model and its key - else a many-to-many's related object.
    pointing, key = {"notes": (TeamNote, "team"), "posts": (SquadPost, "squad")}.get(relation, (None, None))
    if pointing is not None:
        related = pointing.objects.create(text="hello", **{key: owner})
    else:
        related = (
            EmailUser.objects.create(username="alice", email="a@example.com")
            if model is not Roster
            else Tag.objects.create(label="x")
        )
        getattr(owner, relation).add(related)
    done = threading.Event()

    def remove():
        try:
            if pointing is not None:
                pointing.objects.filter(pk=related.pk).delete()
            else:
                getattr(model.objects.get(pk=owner.pk), relation).remove(related)
        finally:
            connections.close_all()
            done.set()

    with transaction.atomic():
        model.objects.select_for_update().get(pk=owner.pk)
        remover = threading.Thread(target=remove)
        remover.start()
        waited = False
        with connection.cursor() as cursor:
            for _ in range(500):
                # Statistics are read once per transaction unless the snapshot is dropped.
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
                if cursor.fetchone()[0]:
                    waited = True
                    break
                time.sleep(0.01)
        assert waited and not done.is_set()
    remover.join(10)

    assert done.is_set()
    assert not getattr(owner, relation).exists()
