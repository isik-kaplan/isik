"""A version that covers many-to-many relations - computed in SQL, its relations' tables locking the owner."""

import hashlib
import threading
import time
import uuid

import pytest
from django.core.checks import Warning
from django.db import connection, connections, transaction
from django.test.utils import isolate_apps
from django.utils.functional import SimpleLazyObject

from isik.django.apps.common.checks import every_concurrency_relation_is_locked
from isik.django.apps.common.db import models as base_models
from isik.django.apps.common.db.concurrency import (
    ETAG_ANNOTATION,
    _relation,
    _token_expression,
    assert_concurrency_field_changes,
    concurrency_etag,
    concurrency_etag_expression,
    concurrency_relations,
    concurrency_token_problems,
    current_concurrency_etag,
    with_concurrency_etags,
)
from tests.testapp.models import EmailUser, Tag, Team, TeamLabel, VersionedNote, Widget


pytestmark = pytest.mark.django_db


@pytest.fixture
def team():
    return Team.objects.create(name="core")


@pytest.fixture
def alice():
    return EmailUser.objects.create(username="alice", email="alice@example.com")


@pytest.fixture
def tag():
    return Tag.objects.create(label="red")


def etag(team):
    return current_concurrency_etag(Team, team.pk)


class TestWhichRelations:
    def test_the_ones_named(self):
        assert concurrency_relations(Team) == ("members", "labels")

    def test_none_by_default(self):
        assert concurrency_relations(Widget) == ()

    def test_named_ones_that_arent_many_to_many_fields_are_problems(self):
        @isolate_apps("tests.testapp")
        def problems():
            from django.db import models

            class Odd(models.Model):
                count = models.IntegerField()
                concurrency_field = "count"
                concurrency_relations = ("count", "missing")

                class Meta:
                    app_label = "testapp"

            return [problem for problem in concurrency_token_problems(Odd) if "relation" in problem]

        assert problems() == [
            "testapp.Odd's concurrency relation 'count' is not one of its many-to-many fields.",
            "testapp.Odd's concurrency relation 'missing' is not one of its many-to-many fields.",
        ]

    def test_ones_that_are_are_no_problem(self):
        assert concurrency_token_problems(Team) == []


class TestTheETag:
    def test_is_a_strong_opaque_tag(self, team):
        value = etag(team)

        assert value.startswith('"') and value.endswith('"')
        assert len(value) == 34

    def test_moves_when_the_row_does(self, team):
        before = etag(team)
        Team.objects.filter(pk=team.pk).update(name="other")

        assert etag(team) != before

    def test_moves_when_a_member_is_added_or_removed(self, team, alice):
        empty = etag(team)
        team.members.add(alice)
        joined = etag(team)
        team.members.remove(alice)

        assert empty != joined
        # The same rows, the same version: nothing a write made against it would lose.
        assert etag(team) == empty

    def test_moves_when_a_through_rows_own_field_does(self, team, tag):
        team.labels.add(tag, through_defaults={"weight": 1})
        before = etag(team)
        TeamLabel.objects.filter(team=team).update(weight=2)

        assert etag(team) != before

    def test_doesnt_move_updated_at(self, team, alice):
        team.refresh_from_db()
        stamped = team.updated_at
        team.members.add(alice)
        team.refresh_from_db()

        assert team.updated_at == stamped

    def test_is_another_rows_own(self, team, alice):
        other = Team.objects.create(name="other")
        before = etag(other)
        team.members.add(alice)

        assert etag(other) == before

    def test_reads_the_same_wherever_it_is_read(self, team, alice):
        team.members.add(alice)

        annotated = with_concurrency_etags(Team.objects.filter(pk=team.pk)).get()

        assert concurrency_etag(annotated) == etag(team) == concurrency_etag(Team.objects.get(pk=team.pk))

    def test_without_the_annotation_its_read_from_the_database(self, team, alice):
        stale = Team.objects.get(pk=team.pk)
        team.members.add(alice)

        assert concurrency_etag(stale) == etag(team)
        assert not hasattr(stale, ETAG_ANNOTATION)

    def test_a_lazy_object_reads_through(self, team):
        assert concurrency_etag(SimpleLazyObject(lambda: Team.objects.get(pk=team.pk))) == etag(team)

    def test_a_row_that_isnt_there_has_none(self, team):
        pk = team.pk
        team.delete()

        assert current_concurrency_etag(Team, pk) is None
        assert concurrency_etag(team.__class__(pk=pk)) is None

    def test_a_model_covering_nothing_is_left_as_it_is(self):
        queryset = Widget.objects.all()

        assert with_concurrency_etags(queryset) is queryset


class TestTheExpression:
    def test_hashes_a_versions_number(self):
        VersionedNote.objects.create(text="a")

        computed = VersionedNote.objects.annotate(etag=concurrency_etag_expression(VersionedNote)).get().etag

        assert computed == hashlib.md5(b"1").hexdigest()

    def test_hashes_a_datetimes_whole_microseconds_as_its_plain_etag_has_them(self):
        widget = Widget.objects.create(name="bolt")

        computed = Widget.objects.annotate(etag=concurrency_etag_expression(Widget)).get().etag

        assert computed == hashlib.md5(concurrency_etag(widget).strip('"').encode()).hexdigest()

    def test_reads_a_datetime_as_whole_microseconds_in_sql(self):
        sql = str(Widget.objects.annotate(token=_token_expression(Widget)).query)

        assert '(EXTRACT(EPOCH FROM "testapp_widget"."updated_at") * 1000000)::bigint' in sql

    def test_finds_each_relation_by_its_own_name(self):
        assert _relation(Team, "members").name == "members"
        assert _relation(Team, "labels").name == "labels"

    def test_an_annotated_etag_is_the_one_read_with_the_row(self, team, alice):
        annotated = with_concurrency_etags(Team.objects.filter(pk=team.pk)).get()
        read = etag(team)
        team.members.add(alice)

        assert concurrency_etag(annotated) == read != etag(team)


class TestTheHelper:
    def test_passes_for_relations_that_move_it(self, team, alice, tag):
        assert_concurrency_field_changes(team, related={"members": alice, "labels": (tag, {"weight": 3})})

    def test_wants_an_object_for_every_relation(self, team, alice):
        with pytest.raises(AssertionError) as raised:
            assert_concurrency_field_changes(team, related={"members": alice})

        assert (
            str(raised.value) == "testapp.Team's version covers 'labels' - pass related={'labels': <an object to add>}."
        )

    def test_wants_one_without_a_mapping_at_all(self, team):
        with pytest.raises(AssertionError, match="covers 'members'"):
            assert_concurrency_field_changes(team)

    @pytest.mark.parametrize(("frozen", "write"), [(1, "add()"), (2, "remove()"), (3, "add()"), (4, "clear()")])
    def test_names_the_write_that_left_it_still(self, team, alice, tag, monkeypatch, frozen, write):
        from isik.django.apps.common.db import concurrency

        reads = []
        real = concurrency.current_concurrency_etag

        def read(model, pk):
            reads.append(real(model, pk))
            # The read after the frozen write repeats the one before it.
            return reads[-2] if len(reads) == frozen + 1 else reads[-1]

        monkeypatch.setattr(concurrency, "current_concurrency_etag", read)

        with pytest.raises(AssertionError) as raised:
            assert_concurrency_field_changes(team, related={"members": alice, "labels": (tag, {"weight": 1})})

        assert str(raised.value) == f"testapp.Team's ETag was {reads[-2]} still after members.{write}."


class TestTheLockTriggers:
    def test_one_per_relation_on_a_proxy_in_the_owners_app(self):
        locks = {(owner, relation): (proxy, trigger) for owner, relation, proxy, trigger in base_models.relation_locks}

        proxy, trigger = locks[Team, "members"]
        assert proxy.__name__ == "TeamMembersConcurrencyLock"
        assert proxy._meta.proxy and proxy._meta.app_label == "testapp"
        assert proxy._meta.db_table == Team.members.through._meta.db_table
        assert trigger.name == "lock_members_owner"
        assert locks[Team, "labels"][0]._meta.db_table == TeamLabel._meta.db_table

    def test_lock_the_owner_and_write_nothing(self):
        trigger = base_models._relation_lock_trigger(Team, Team._meta.get_field("labels"), TeamLabel)

        assert trigger.when == base_models.pgtrigger.After
        assert str(trigger.operation) == "INSERT OR UPDATE OR DELETE"
        assert trigger.func == (
            'IF TG_OP <> \'INSERT\' THEN PERFORM 1 FROM "testapp_team" WHERE "id" = OLD."team_id" FOR KEY SHARE; '
            'END IF; IF TG_OP <> \'DELETE\' THEN PERFORM 1 FROM "testapp_team" WHERE "id" = NEW."team_id" '
            "FOR KEY SHARE; END IF; RETURN NULL;"
        )

    def test_a_model_defined_now_gets_its_own(self, monkeypatch):
        monkeypatch.setattr(base_models, "relation_locks", [])
        name = f"Crew{uuid.uuid4().hex[:8]}"

        @isolate_apps("tests.testapp")
        def define():
            from django.db import models

            meta = type("Meta", (), {"app_label": "testapp"})
            # To itself, so it resolves inside the isolated registry; to a model loaded later, the lock
            # waits for it.
            fields = {
                "tags": models.ManyToManyField("self", symmetrical=False, related_name="+"),
                "concurrency_relations": ("tags",),
            }
            return type(name, (models.Model,), {"__module__": __name__, "Meta": meta, **fields})

        owner = define()

        ((locked, relation, proxy, trigger),) = base_models.relation_locks
        assert (locked, relation) == (owner, "tags")
        assert proxy.__name__ == f"{name}TagsConcurrencyLock"
        assert proxy.__module__ == __name__
        assert proxy._meta.proxy is True
        assert proxy._meta.app_label == "testapp"
        assert proxy._meta.concrete_model is owner.tags.through
        assert proxy._meta.triggers == [trigger]
        assert trigger.name == "lock_tags_owner"
        assert f'= NEW."from_{name.lower()}_id"' in trigger.func

    def test_a_relation_to_a_model_loaded_later_waits_for_it(self, monkeypatch):
        monkeypatch.setattr(base_models, "relation_locks", [])
        suffix = uuid.uuid4().hex[:8]

        @isolate_apps("tests.testapp")
        def define():
            from django.db import models

            meta = type("Meta", (), {"app_label": "testapp"})
            fields = {"later": models.ManyToManyField(f"Later{suffix}"), "concurrency_relations": ("later",)}
            type(f"Early{suffix}", (models.Model,), {"__module__": __name__, "Meta": meta, **fields})
            waiting = list(base_models.relation_locks)
            type(
                f"Later{suffix}",
                (models.Model,),
                {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"})},
            )
            return waiting

        assert define() == []
        ((owner, relation, proxy, trigger),) = base_models.relation_locks
        assert (owner.__name__, relation) == (f"Early{suffix}", "later")
        assert f'= NEW."early{suffix}_id"' in trigger.func

    def test_a_relation_named_wrongly_gets_none(self):
        @isolate_apps("tests.testapp")
        def define():
            from django.db import models

            class Wrong(models.Model):
                concurrency_relations = ("nothing",)

                class Meta:
                    app_label = "testapp"

            return Wrong

        count = len(base_models.relation_locks)
        define()

        assert len(base_models.relation_locks) == count

    def test_are_installed_by_the_migrations(self):
        assert every_concurrency_relation_is_locked(None, databases=["default"]) == []

    def test_one_missing_is_a_warning_naming_it(self, monkeypatch):
        owner, relation, proxy, trigger = next(lock for lock in base_models.relation_locks if lock[1] == "members")
        monkeypatch.setattr(base_models, "relation_locks", [(owner, relation, proxy, trigger)])
        asked = []

        def status(self, model, database):
            asked.append((model, database))
            return ("OUTDATED", True)

        monkeypatch.setattr(type(trigger), "get_installation_status", status)

        (warning,) = every_concurrency_relation_is_locked(None, databases=["default"])

        assert asked == [(proxy, "default")]
        assert isinstance(warning, Warning)
        assert warning.id == "isik.W002"
        assert warning.msg == (
            "testapp.Team's version covers 'members', but the trigger locking it on testapp_team_members is "
            "outdated on database 'default' - a change to it can slip past a write's If-Match."
        )
        assert warning.hint == "Run makemigrations and migrate - the trigger is in the migrations of testapp."

    def test_an_installed_one_doesnt_stop_the_next_being_checked(self, monkeypatch):
        statuses = iter([("INSTALLED", True), ("UNINSTALLED", False)])
        monkeypatch.setattr(
            type(base_models.relation_locks[0][3]),
            "get_installation_status",
            lambda self, model, database: next(statuses),
        )
        monkeypatch.setattr(base_models, "relation_locks", base_models.relation_locks[:2])

        (warning,) = every_concurrency_relation_is_locked(None, databases=["default"])

        assert "is uninstalled on database 'default'" in warning.msg

    def test_a_database_the_model_is_kept_off_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(
            type(base_models.relation_locks[0][3]),
            "get_installation_status",
            lambda self, model, database: ("UNALLOWED", None),
        )

        assert every_concurrency_relation_is_locked(None, databases=["default"]) == []

    def test_without_databases_nothing_is_asked(self, monkeypatch):
        monkeypatch.setattr(type(base_models.relation_locks[0][3]), "get_installation_status", None)

        assert every_concurrency_relation_is_locked(None) == []


@pytest.mark.django_db(transaction=True)
def test_removing_a_member_waits_for_a_write_holding_the_owner():
    team = Team.objects.create(name="core")
    alice = EmailUser.objects.create(username="alice", email="alice@example.com")
    team.members.add(alice)
    done = threading.Event()

    def remove():
        try:
            Team.objects.get(pk=team.pk).members.remove(alice)
        finally:
            connections.close_all()
            done.set()

    with transaction.atomic():
        Team.objects.select_for_update().get(pk=team.pk)
        remover = threading.Thread(target=remove)
        remover.start()
        waited = False
        with connection.cursor() as cursor:
            for _ in range(500):
                # Statistics are read once per transaction unless the snapshot is dropped - and this
                # one holds the lock the other is waiting on.
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
    assert not team.members.exists()
