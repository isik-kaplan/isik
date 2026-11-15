"""Declaring what syncs - syncs(), Memberships, and the scopes a person is in."""

import dataclasses
import uuid

import pytest
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.sync import declarations
from isik.django.apps.sync.declarations import (
    Memberships,
    Synced,
    log_relation_parents,
    memberships_by_kind,
    scopes_of,
    synced_models,
    syncs,
)
from isik.django.apps.sync.triggers import MembershipTrigger, SyncLogTrigger
from tests.django.apps.sync import support
from tests.testapp.models import SyncNote, SyncProject, SyncTask, SyncTeam, SyncTeamMembership


ALICE = "0190a000-0000-7000-8000-a00000000001"
TEAM, OTHER_TEAM = "0190a000-0000-7000-8000-b00000000007", "0190a000-0000-7000-8000-b00000000008"
TEAMS = Memberships("testapp.SyncTeamMembership", user="user", scope="team", condition=models.Q(active=True))


@pytest.fixture
def own_registry(monkeypatch):
    """syncs() declaring into a registry of the test's own - the project's models declared there too."""
    monkeypatch.setattr(declarations, "SYNCED", dict(declarations.SYNCED))
    monkeypatch.setattr(declarations, "_MEMBERSHIP_TRIGGERS", set(declarations._MEMBERSHIP_TRIGGERS))


def throwaway(**fields):
    name = f"Synced{uuid.uuid4().hex[:8]}"
    attributes = {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "testapp"}), **fields}
    return type(name, (models.Model,), attributes)


def synced(model, scope, kind=None):
    return Synced(name="x", model=model, scope=scope, kind_given=kind, memberships=None, viewset=None, indexed=None)


class TestAScope:
    def test_a_key_to_the_user_model_is_a_person_s_own(self):
        assert synced(SyncTask, "owner").kind == "user"

    def test_a_key_to_another_model_is_named_by_it(self):
        assert synced(SyncNote, "team").kind == "syncteam"

    def test_a_kind_given_wins(self):
        assert synced(SyncNote, "team", kind="team").kind == "team"

    @pytest.mark.parametrize("field", ["title", "nothing"])
    def test_a_field_that_isn_t_a_key_has_none(self, field):
        assert synced(SyncTask, field).kind is None

    def test_a_row_s_scope_is_its_value_under_its_kind(self):
        declared = synced(SyncTask, "owner")

        assert declared.scope_of("7") == "user:7"
        assert declared.scope_of(None) is None
        assert declared.scope_field is SyncTask._meta.get_field("owner")


class TestSyncs:
    def test_the_project_s_models_are_declared_by_name_in_order(self):
        assert list(synced_models()) == ["boards", "notes", "projects", "tasks"]
        assert synced_models()["notes"].memberships == TEAMS
        assert SyncNote.synced is synced_models()["notes"]

    @isolate_apps("tests.testapp")
    def test_named_by_the_model_unless_given_with_every_argument(self, own_registry):
        model = syncs(scope="owner", viewset="a.ViewSet", indexed=[["owner"]])(
            throwaway(owner=models.ForeignKey("testapp.EmailUser", on_delete=models.CASCADE, related_name="+"))
        )

        declared = synced_models()[model._meta.model_name]
        assert (declared.model, declared.scope) == (model, "owner")
        assert (declared.kind_given, declared.memberships) == (None, None)
        assert (declared.viewset, declared.indexed) == ("a.ViewSet", (("owner",),))
        assert [type(trigger) for trigger in model._meta.triggers] == [SyncLogTrigger]

    @isolate_apps("tests.testapp")
    def test_indexed_unset_is_none(self, own_registry):
        model = syncs("loose", scope="owner")(throwaway(owner=models.UUIDField()))

        assert synced_models()["loose"].indexed is None
        assert model.synced.name == "loose"

    @isolate_apps("tests.testapp")
    def test_a_name_another_model_syncs_by_is_refused(self, own_registry):
        with pytest.raises(ValueError, match=r"^tasks is synced already, by testapp\.SyncTask\.$"):
            syncs("tasks", scope="owner")(throwaway(owner=models.UUIDField()))

    @isolate_apps("tests.testapp")
    def test_memberships_get_one_trigger_per_kind(self, own_registry):
        member = throwaway(
            user=models.ForeignKey("testapp.EmailUser", on_delete=models.CASCADE, related_name="+"),
            group=models.UUIDField(),
        )
        group = throwaway()
        teams = Memberships(member, user="user", scope="group")
        for _ in range(2):
            syncs(scope="group", memberships=teams)(
                throwaway(group=models.ForeignKey(group, on_delete=models.CASCADE, related_name="+"))
            )

        [trigger] = [each for each in member._meta.triggers if isinstance(each, MembershipTrigger)]
        assert (trigger.kind, trigger.memberships, trigger.name) == (
            group._meta.model_name,
            teams,
            f"sync_{group._meta.model_name}_membership",
        )

    @isolate_apps("tests.testapp")
    def test_memberships_of_a_kind_given_wait_for_their_model_alone(self, own_registry):
        member = throwaway(user=models.UUIDField(), group=models.UUIDField())
        syncs(scope="group", kind="crew", memberships=Memberships(member, user="user", scope="group"))(
            throwaway(group=models.UUIDField())
        )

        assert [each.kind for each in member._meta.triggers] == ["crew"]

    @isolate_apps("tests.testapp")
    @pytest.mark.parametrize("field", [models.UUIDField(), None])
    def test_memberships_scoped_by_no_key_wait_for_their_model_alone(self, own_registry, field):
        member = throwaway(user=models.UUIDField(), group=models.UUIDField())
        syncs(scope="group", memberships=Memberships(member, user="user", scope="group"))(
            throwaway(**({} if field is None else {"group": field}))
        )

        assert [each.kind for each in member._meta.triggers] == [None]


class TestMemberships:
    def test_named_by_label_or_model(self):
        assert TEAMS.label == "testapp.SyncTeamMembership"
        assert Memberships(SyncTeamMembership, user="user", scope="team").label == "testapp.SyncTeamMembership"
        assert TEAMS.resolve() is SyncTeamMembership
        assert TEAMS.attname("team") == "team_id"

    def test_by_kind_from_the_first_declaring_them(self):
        assert memberships_by_kind() == {"team": TEAMS}


@pytest.mark.django_db
class TestScopes:
    def test_a_person_is_in_their_own_and_each_membership_s(self):
        alice = support.user(ALICE)
        support.grant(ALICE, f"team:{TEAM}")
        SyncTeamMembership.objects.create(user=alice, team=support.team(OTHER_TEAM), active=False)

        assert scopes_of(alice) == [f"user:{ALICE}", f"team:{TEAM}"]

    def test_with_no_memberships_only_their_own(self, monkeypatch):
        monkeypatch.setattr(declarations, "memberships_by_kind", dict)

        assert scopes_of(support.user(ALICE)) == [f"user:{ALICE}"]


class TestRelationParents:
    def test_each_covered_relation_logs_its_synced_row_once(self):
        log_relation_parents()

        assert declarations.PARENT_LOGS == [("boards", "teams")]
        assert SyncBoardTeamsSyncLog()._meta.proxy

    def test_a_relation_that_isn_t_one_is_left_to_the_check(self, own_registry, monkeypatch):
        monkeypatch.setattr(declarations, "PARENT_LOGS", list(declarations.PARENT_LOGS))
        monkeypatch.setattr(SyncProject, "concurrency_relations", ("nothing",), raising=False)

        log_relation_parents()

        assert declarations.PARENT_LOGS == [("boards", "teams")]


def SyncBoardTeamsSyncLog():  # noqa: N802 - the model's own name
    from django.apps import apps

    return apps.get_model("testapp", "SyncBoardTeamsSyncLog")


def test_the_team_model_is_the_teams_scope():
    assert SyncTeam._meta.model_name == "syncteam"


def test_a_viewset_named_by_path_or_class_serves_the_model():
    from isik.django.apps.sync.reading import viewset_of
    from tests.testapp.sync_views import TaskViewSet

    named = dataclasses.replace(SyncTask.synced, viewset="tests.testapp.sync_views.TaskViewSet")

    assert viewset_of(named) is TaskViewSet
    assert viewset_of(dataclasses.replace(SyncTask.synced, viewset=TaskViewSet)) is TaskViewSet
    assert viewset_of(SyncTask.synced) is TaskViewSet


def test_the_log_model_setting_set_already_is_kept(settings):
    from isik.django.apps.sync.swapping import get_sync_log_model, set_defaults

    settings.ISIK_SYNC_LOG_MODEL = "isik_sync.SyncLogEntry"
    set_defaults()

    assert get_sync_log_model()._meta.label == "isik_sync.SyncLogEntry"


def test_memberships_with_no_condition_take_every_row():
    teams = Memberships("testapp.SyncTeamMembership", user="user", scope="team")
    func = MembershipTrigger(kind="team", memberships=teams).get_func(SyncTeamMembership)
    conditioned = MembershipTrigger(kind="team", memberships=TEAMS).get_func(SyncTeamMembership)

    assert '"active"' not in func
    assert 'NEW."active"' in conditioned
