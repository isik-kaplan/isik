"""isik.E027 - a sync setting or syncs() declaration that can't work."""

import dataclasses
from datetime import timedelta

import pytest
from django.db import models
from rest_framework import serializers
from rest_framework.throttling import SimpleRateThrottle

from isik.django.apps.sync import checks, declarations
from isik.django.apps.sync.checks import model_problems, setting_problems, sync_can_work, sync_problems
from isik.django.apps.sync.declarations import Memberships
from isik.django.conf import override_isik
from tests.testapp.models import SyncNote, SyncTask
from tests.testapp.sync_views import NoteSerializer, TaskSerializer, TaskViewSet


AREA = "ISIK['DJANGO']['SYNC']"


def problems(**sync):
    with override_isik(DJANGO={"SYNC": sync}):
        return setting_problems()


class TestTheSettings:
    def test_unset_is_fine(self):
        assert sync_problems() == []

    def test_keys_it_doesn_t_know(self):
        assert problems(B=1, A=2) == [f"{AREA} has keys it doesn't know: A, B."]

    @pytest.mark.parametrize("name", ["RETENTION", "OFFLINE_PERIOD", "LAG_WARNING"])
    @pytest.mark.parametrize("value", [5, timedelta(0), timedelta(seconds=-1)])
    def test_a_period_is_a_positive_timedelta(self, name, value):
        assert problems(**{name: value}) == [f"{AREA}['{name}'] is a timedelta longer than none, or None."]

    @pytest.mark.parametrize("value", [0, True, "5", 1.5])
    def test_a_page_limit_is_a_whole_number(self, value):
        assert problems(PAGE_LIMIT=value) == [f"{AREA}['PAGE_LIMIT'] is a whole number of at least 1, or None."]

    def test_one_of_each_set_well_is_fine(self):
        assert problems(RETENTION=timedelta(days=2), OFFLINE_PERIOD=timedelta(days=1), PAGE_LIMIT=1) == []

    @pytest.mark.parametrize("push", [[], {"BACKEND": "x", "LIMITS": {}}])
    def test_push_is_backend_and_options(self, push):
        assert problems(PUSH=push) == [f"{AREA}['PUSH'] is a dict of BACKEND and OPTIONS."]

    def test_a_backend_that_won_t_load(self):
        assert problems(PUSH={"BACKEND": "isik.nothing.Backend"}) == [
            f"{AREA}['PUSH']['BACKEND'] can't be loaded: No module named 'isik.nothing'"
        ]

    @pytest.mark.parametrize("backend", [object, "isik.django.apps.sync.push.paths"])
    def test_a_backend_is_a_push_backend(self, backend):
        assert problems(PUSH={"BACKEND": backend}) == [
            f"{AREA}['PUSH']['BACKEND'] is a PushBackend subclass, or its dotted path."
        ]

    @pytest.mark.parametrize("name", ["MAX_ENTRIES", "MAX_BYTES"])
    def test_a_limit_is_a_whole_number(self, name):
        assert problems(PUSH={"OPTIONS": {name: 0}}) == [
            f"{AREA}['PUSH']['OPTIONS']['{name}'] is a whole number of at least 1, or None."
        ]

    def test_max_bytes_fits_what_django_takes(self, settings):
        settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 100

        assert problems(PUSH={"OPTIONS": {"MAX_BYTES": 100}}) == []
        assert problems(PUSH={"OPTIONS": {"MAX_BYTES": 101}}) == [
            f"{AREA}['PUSH']['OPTIONS']['MAX_BYTES'] is 101, and DATA_UPLOAD_MAX_MEMORY_SIZE refuses a body over "
            "100 before a push sees it - make it at most that."
        ]
        settings.DATA_UPLOAD_MAX_MEMORY_SIZE = None
        assert problems(PUSH={"OPTIONS": {"MAX_BYTES": 10**9}}) == []

    def test_a_throttle_scope_has_a_rate(self, monkeypatch):
        monkeypatch.setattr("rest_framework.settings.api_settings.DEFAULT_THROTTLE_RATES", {"push": "1/s"})

        assert problems(PUSH={"OPTIONS": {"THROTTLE_SCOPE": "push"}}) == []
        assert problems(PUSH={"OPTIONS": {"THROTTLE_SCOPE": "pushes"}}) == [
            f"{AREA}['PUSH']['OPTIONS']['THROTTLE_SCOPE'] is 'pushes', which DEFAULT_THROTTLE_RATES gives no rate."
        ]
        assert SimpleRateThrottle

    def test_retention_holds_the_offline_period(self):
        assert problems(RETENTION=timedelta(days=1), OFFLINE_PERIOD=timedelta(days=1)) == []
        assert problems(RETENTION=timedelta(hours=23), OFFLINE_PERIOD=timedelta(days=1)) == [
            f"{AREA}['RETENTION'] is shorter than its OFFLINE_PERIOD: a client offline that long comes back to a "
            "cursor pruning passed, and pulls everything again."
        ]
        assert problems(OFFLINE_PERIOD=timedelta(days=1)) == []

    def test_claims_outlive_the_offline_period(self):
        message = (
            f"ISIK['DJANGO']['IDEMPOTENCY']['RETENTION'] is no longer than {AREA}['OFFLINE_PERIOD']: a client offline "
            "that long pushes keys whose claims are gone, and a write whose answer it lost runs twice."
        )
        with override_isik(DJANGO={"IDEMPOTENCY": {"RETENTION": timedelta(days=1)}}):
            assert problems(OFFLINE_PERIOD=timedelta(days=1)) == [message]
            assert problems(OFFLINE_PERIOD=timedelta(hours=23)) == []
            assert problems() == []


@pytest.fixture
def declared(monkeypatch):
    """Puts a changed declaration of a model in the registry, in place of its own."""
    registry = dict(declarations.SYNCED)
    monkeypatch.setattr(declarations, "SYNCED", registry)

    def put(model, **changes):
        synced = dataclasses.replace(model.synced, **changes)
        registry[synced.name] = synced
        return synced

    return put


class Nested(serializers.ModelSerializer):
    owner = NoteSerializer()

    class Meta:
        model = SyncTask
        fields = ["owner", "title"]


class TestTheDeclarations:
    def test_a_scope_that_isn_t_a_field(self, declared):
        declared(SyncTask, scope="nothing")

        assert model_problems() == ["testapp.SyncTask is scoped by 'nothing', which isn't a field."]

    def test_a_scope_that_isn_t_a_key_needs_a_kind(self, declared):
        declared(SyncTask, scope="title")

        assert model_problems() == [
            "testapp.SyncTask is scoped by title, which isn't a key - say its kind: syncs(kind=...)."
        ]

    def test_a_kind_is_a_plain_name(self, declared):
        declared(SyncTask, kind_given="My Kind")

        assert model_problems()[0] == "testapp.SyncTask's scope kind 'My Kind' isn't a plain lowercase name."

    def test_a_person_s_own_scope_has_no_memberships(self, declared):
        declared(SyncTask, memberships=SyncNote.synced.memberships)

        assert model_problems() == [
            "testapp.SyncTask names memberships for the kind user - a person is in their own user scope alone."
        ]

    def test_memberships_in_something_that_isn_t_a_model(self, declared):
        declared(SyncNote, memberships=Memberships("testapp.Nothing", user="user", scope="team"))

        assert model_problems() == ["testapp.SyncNote names memberships in testapp.Nothing, which isn't a model."]

    def test_memberships_by_fields_that_aren_t(self, declared):
        declared(SyncNote, memberships=Memberships("testapp.SyncTeamMembership", user="person", scope="crew"))

        assert model_problems() == [
            "testapp.SyncNote names memberships by testapp.SyncTeamMembership.person, which isn't a field.",
            "testapp.SyncNote names memberships by testapp.SyncTeamMembership.crew, which isn't a field.",
        ]

    def test_a_kind_no_model_names_memberships_of(self, declared):
        declared(SyncNote, memberships=None)

        assert model_problems() == [
            "Models are scoped by the kind team, and none names its memberships: syncs(memberships=...)."
        ]

    def test_a_kind_named_two_memberships(self, declared):
        declared(SyncTask, name="tasks", scope="project", kind_given="team")
        declared(
            SyncTask,
            name="more",
            scope="project",
            kind_given="team",
            memberships=Memberships("testapp.SyncTeamMembership", user="user", scope="team"),
        )

        assert model_problems() == ["Models scoped by the kind team name different memberships."]

    def test_an_etag_from_concurrency_parts(self, declared, monkeypatch):
        monkeypatch.setattr(checks, "concurrency_strategy", lambda model: "parts" if model is SyncTask else None)

        assert model_problems() == [
            "testapp.SyncTask is synced, and its ETag reads concurrency_parts() - what they read changes with no "
            "change the feed hears of."
        ]

    def test_no_concurrency_token(self, monkeypatch):
        monkeypatch.setattr(checks, "concurrency_field_name", lambda model: None if model is SyncTask else "x")

        assert model_problems() == [
            "testapp.SyncTask is synced, and has no concurrency token - the feed sends each row's ETag."
        ]

    def test_a_covered_relation_whose_rows_don_t_log_it(self, monkeypatch):
        covered = {SyncTask: ("teams", "labels")}
        monkeypatch.setattr(checks, "concurrency_relations", lambda model: covered.get(model, ()))

        assert model_problems() == [
            "testapp.SyncTask is synced, and its version covers teams, labels, whose rows log no change of it - a "
            "change to one moves its ETag with no change the feed hears of."
        ]

    def test_the_covered_relation_logging_its_row_is_fine(self):
        assert declarations.PARENT_LOGS == [("boards", "teams")]
        assert model_problems() == []

    def test_no_viewset(self, monkeypatch):
        viewset_of = checks.viewset_of
        monkeypatch.setattr(
            checks, "viewset_of", lambda synced: None if synced.model is SyncTask else viewset_of(synced)
        )

        assert model_problems() == [
            "testapp.SyncTask is synced, and no viewset serves it - register one, or name it: syncs(viewset=...)."
        ]

    def test_a_representation_nested_and_missing_its_key(self, monkeypatch):
        monkeypatch.setattr(TaskViewSet, "serializer_class", Nested)

        assert model_problems() == [
            "testapp.SyncTask is synced, and its REST representation nests owner - keep keys as ids, nothing nested.",
            "testapp.SyncTask is synced, and its REST representation leaves out id - a client needs its key and its "
            "scope.",
            "testapp.SyncTask is synced, and Nested creates its rows under ids of its own - give it "
            "ClientPrimaryKeyMixin, so a client offline can make a row's id.",
        ]

    def test_the_task_serializer_is_what_a_synced_model_needs(self):
        assert TaskSerializer.Meta.fields == ["id", "owner", "title", "done", "project"]


def test_registered_as_isik_e027(monkeypatch):
    monkeypatch.setattr(checks, "sync_problems", lambda: ["one"])

    assert [(error.id, error.msg, error.hint) for error in sync_can_work(app_configs=None)] == [
        ("isik.E027", "one", "See isik.django.apps.sync.")
    ]


def test_models_are_the_test_project_s():
    assert models.Q(active=True) == SyncNote.synced.memberships.condition
