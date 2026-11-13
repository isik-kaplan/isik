"""ScopedModel and ScopedForeignKey - rows the database keeps in one scope - and the check holding keys to them."""

import types
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models import ProtectedError
from django.test.utils import isolate_apps

from isik.django.apps.common.db import scopes
from isik.django.apps.common.db.scopes import (
    ScopedForeignKey,
    ScopedForeignKeyConstraint,
    ScopedModel,
    scoped_key_problems,
)
from tests.testapp.models import Attempt, AttemptResponse, Campaign, Code, CodedAttempt


pytestmark = pytest.mark.django_db


@pytest.fixture
def north():
    return Campaign.objects.create(name="north")


@pytest.fixture
def south():
    return Campaign.objects.create(name="south")


@pytest.fixture
def knock(north):
    return Attempt.objects.create(campaign=north, door="12")


def constraint(model, name):
    return next(each for each in model._meta.constraints if each.name == name)


class TestTheDatabase:
    def test_a_response_in_its_attempts_campaign(self, north, knock):
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, answer="yes")

        assert AttemptResponse.objects.get(pk=response.pk).attempt == knock

    def test_one_in_another_campaign_is_refused_at_commit_when_deferred(self, south, knock):
        with pytest.raises(IntegrityError), transaction.atomic():
            AttemptResponse.objects.create(campaign=south, attempt_id=knock.pk, answer="yes")
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_one_in_another_campaign_is_refused_at_once_when_immediate(self, north, south, knock):
        other = Attempt.objects.create(campaign=south, door="3")

        with pytest.raises(IntegrityError), transaction.atomic():
            AttemptResponse.objects.create(campaign=north, attempt=knock, follow_up_id=other.pk, answer="yes")

    def test_raw_sql_moving_a_row_out_of_its_attempts_campaign_is_refused(self, north, south, knock):
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, answer="yes")

        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {AttemptResponse._meta.db_table} SET campaign_id = %s WHERE id = %s", [south.pk, response.pk]
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_the_constraints_and_index_postgres_holds(self):
        with connection.cursor() as cursor:
            found = connection.introspection.get_constraints(cursor, AttemptResponse._meta.db_table)

        key = found["testapp_attemptresponse_attempt_in_scope"]
        assert (key["foreign_key"], key["columns"]) == (
            ("testapp_attempt", "campaign_id"),
            ["campaign_id", "attempt_id"],
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT condeferrable, condeferred FROM pg_constraint WHERE conname = %s",
                ["testapp_attemptresponse_attempt_in_scope"],
            )
            assert cursor.fetchone() == (True, True)
            cursor.execute(
                "SELECT condeferrable FROM pg_constraint WHERE conname = %s",
                ["testapp_attemptresponse_follow_up_in_scope"],
            )
            assert cursor.fetchone() == (False,)
        index = next(
            each for each in found.values() if each["index"] and each["columns"] == ["campaign_id", "attempt_id"]
        )
        assert not index["unique"]
        with connection.cursor() as cursor:
            targets = connection.introspection.get_constraints(cursor, Attempt._meta.db_table)
        assert targets["testapp_attempt_in_scope"]["unique"]
        assert targets["testapp_attempt_in_scope"]["columns"] == ["campaign_id", "id"]


class TestTheRelation:
    def test_assigning_the_attempt_sets_the_campaign(self, north, south, knock):
        response = AttemptResponse(campaign=south, answer="yes")

        response.attempt = knock

        assert (response.attempt_id, response.campaign_id) == (knock.pk, north.pk)

    def test_traversal(self, north, knock):
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, answer="yes")

        assert list(knock.responses.all()) == [response]
        assert AttemptResponse.objects.select_related("attempt").get(pk=response.pk).attempt.door == "12"
        assert list(AttemptResponse.objects.filter(attempt=knock)) == [response]
        assert list(Attempt.objects.filter(responses__answer="yes")) == [knock]

    def test_protect(self, north, knock):
        AttemptResponse.objects.create(campaign=north, attempt=knock, answer="yes")

        with pytest.raises(ProtectedError):
            knock.delete()

    def test_set_null(self, north, knock):
        later = Attempt.objects.create(campaign=north, door="14")
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, follow_up=later, answer="yes")

        later.delete()

        response.refresh_from_db()
        assert response.follow_up_id is None
        assert response.follow_up is None

    def test_refreshing_forgets_the_row_it_read(self, north, knock):
        later = Attempt.objects.create(campaign=north, door="14")
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, follow_up=later, answer="yes")
        AttemptResponse.objects.filter(pk=response.pk).update(follow_up_id=None)

        response.refresh_from_db(fields=["answer"])
        assert response.follow_up == later
        response.refresh_from_db(fields=["follow_up_id"])
        assert response.follow_up is None

    def test_refreshing_forgets_a_key_django_itself_would_keep(self, monkeypatch, north, knock):
        # Django before 6.0 keeps a key's cached row; this stands in for it, so the forgetting is isik's alone.
        seen = []

        def django_refresh(self, using=None, fields=None, from_queryset=None):
            seen.append((using, fields, from_queryset))

        later = Attempt.objects.create(campaign=north, door="14")
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, follow_up=later, answer="yes")
        attempt, follow_up = response.attempt, response.follow_up
        monkeypatch.setattr(models.Model, "refresh_from_db", django_refresh)
        queryset = AttemptResponse.objects.all()

        response.refresh_from_db(using="default", fields=["answer"], from_queryset=queryset)
        assert (response.attempt, response.follow_up) == (attempt, follow_up)
        assert response._state.fields_cache["follow_up"] is follow_up
        response.refresh_from_db(fields=["follow_up_id"])
        assert "follow_up" not in response._state.fields_cache
        assert response._state.fields_cache["attempt"] is attempt
        response.refresh_from_db()
        assert "attempt" not in response._state.fields_cache

        assert seen == [("default", ["answer"], queryset), (None, ["follow_up_id"], None), (None, None, None)]

    @pytest.mark.parametrize("refreshed", [None, ["follow_up"], ["campaign"]])
    def test_refreshing_the_key_or_its_scope(self, north, knock, refreshed):
        later = Attempt.objects.create(campaign=north, door="14")
        response = AttemptResponse.objects.create(campaign=north, attempt=knock, follow_up=later, answer="yes")
        AttemptResponse.objects.filter(pk=response.pk).update(follow_up_id=None)

        response.refresh_from_db(fields=refreshed and [*refreshed, "follow_up_id"])

        assert response.follow_up is None

    def test_its_column(self):
        column = AttemptResponse._meta.get_field("attempt_id")

        assert isinstance(column, models.UUIDField)
        assert (column.null, column.editable, str(column.verbose_name)) == (False, False, "attempt id")
        assert (column.help_text, column.db_comment) == (
            "The attempt it answers.",
            "The attempt it answers - in its campaign.",
        )
        nullable = AttemptResponse._meta.get_field("follow_up_id")
        assert (nullable.null, nullable.blank, nullable.db_comment) == (True, True, None)

    def test_its_related_fields(self):
        key = AttemptResponse._meta.get_field("attempt")

        assert (key.from_fields, key.to_fields, key.scope) == (
            ["campaign", "attempt_id"],
            ["campaign", None],
            "campaign",
        )
        assert [(local.name, remote.name) for local, remote in key.related_fields] == [
            ("campaign", "campaign"),
            ("attempt_id", "id"),
        ]
        assert key.column_name == "attempt_id"

    def test_deconstructed(self):
        name, path, args, kwargs = AttemptResponse._meta.get_field("attempt").deconstruct()

        assert (name, path, args) == ("attempt", "isik.django.apps.common.db.scopes.ScopedForeignKey", [])
        assert kwargs["scope"] == "campaign"
        assert kwargs["deferrable"] is models.Deferrable.DEFERRED
        assert "from_fields" not in kwargs and "to_fields" not in kwargs and "blank" not in kwargs
        assert "deferrable" not in AttemptResponse._meta.get_field("follow_up").deconstruct()[3]


class TestDeclaring:
    def test_on_a_model_with_no_scope(self):
        with pytest.raises(TypeError) as refused, isolate_apps("tests.testapp"):

            class Loose(models.Model):
                attempt = ScopedForeignKey(Attempt, on_delete=models.CASCADE)

                class Meta:
                    app_label = "testapp"

        assert str(refused.value) == (
            "Loose.attempt is a ScopedForeignKey on a model with no scope - base it on a ScopedModel naming its "
            "scope_field, or give the key scope=."
        )

    def test_a_scope_given(self):
        with isolate_apps("tests.testapp"):

            class Given(models.Model):
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                attempt = ScopedForeignKey(Attempt, on_delete=models.CASCADE, scope="campaign", related_name="+")

                class Meta:
                    app_label = "testapp"

        assert Given._meta.get_field("attempt").from_fields == ["campaign", "attempt_id"]

    @pytest.mark.parametrize("on_delete", [models.SET_DEFAULT, models.SET(None), models.SET(lambda: None)])
    def test_an_on_delete_setting_another_value(self, on_delete):
        with pytest.raises(TypeError) as refused:
            ScopedForeignKey(Attempt, on_delete=on_delete)

        assert str(refused.value) == (
            "A ScopedForeignKey takes CASCADE, PROTECT, RESTRICT, SET_NULL or DO_NOTHING as its on_delete."
        )

    @pytest.mark.parametrize("on_delete", [models.CASCADE, models.PROTECT, models.RESTRICT, models.DO_NOTHING])
    def test_an_on_delete_kept(self, on_delete):
        key = ScopedForeignKey(Attempt, on_delete=on_delete)

        assert key.remote_field.on_delete is on_delete

    def test_set_null_is_the_columns(self):
        key = AttemptResponse._meta.get_field("follow_up")

        assert key.remote_field.on_delete is scopes._set_null_column
        assert key.deconstruct()[3]["on_delete"] is models.SET_NULL
        assert scopes._set_null_column.lazy_sub_objs is True

    def test_kept_out_of_forms(self):
        from django import forms

        assert AttemptResponse._meta.get_field("attempt").editable is False
        assert "editable" not in AttemptResponse._meta.get_field("attempt").deconstruct()[3]
        assert list(forms.modelform_factory(AttemptResponse, fields="__all__").base_fields) == ["campaign", "answer"]

    def test_from_and_to_fields_set_once_on_a_model(self):
        key = ScopedForeignKey(Attempt, on_delete=models.CASCADE)

        assert (key.from_fields, key.to_fields) == ([], [])

    @pytest.mark.parametrize(
        ("given", "message"),
        [
            ({"to_fields": "id"}, "A ScopedForeignKey's from_fields and to_fields are tuples of field names."),
            ({"to_fields": ()}, "A ScopedForeignKey's from_fields and to_fields are tuples of field names."),
            (
                {"to_fields": ["id"], "from_fields": "x_id"},
                "A ScopedForeignKey's from_fields and to_fields are tuples of field names.",
            ),
            (
                {"to_fields": ["id"], "from_fields": ()},
                "A ScopedForeignKey's from_fields and to_fields are tuples of field names.",
            ),
            (
                {"from_fields": ["x_id"]},
                "A ScopedForeignKey's from_fields name its own field for each of to_fields, in their order.",
            ),
            (
                {"from_fields": ["x_id"], "to_fields": ["id", "kind"]},
                "A ScopedForeignKey's from_fields name its own field for each of to_fields, in their order.",
            ),
        ],
    )
    def test_from_and_to_fields_refused(self, given, message):
        with pytest.raises(TypeError) as raised:
            ScopedForeignKey(Attempt, on_delete=models.CASCADE, **given)

        assert str(raised.value) == message

    @pytest.mark.parametrize(
        ("given", "message"),
        [
            (
                {"to_fields": ["id", "kind"], "from_fields": ["code", "kind"]},
                "Keyed.code's from_fields start with its own column, code_id.",
            ),
            (
                {"to_fields": ["id", "campaign"]},
                "Keyed.code leads its key with its scope, campaign - from_fields and to_fields name the rest.",
            ),
            (
                {"to_fields": ["id", "kind"], "from_fields": ["code_id", "campaign"]},
                "Keyed.code leads its key with its scope, campaign - from_fields and to_fields name the rest.",
            ),
        ],
    )
    def test_from_and_to_fields_refused_on_a_model(self, given, message):
        with pytest.raises(TypeError) as raised, isolate_apps("tests.testapp"):

            class Keyed(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                kind = models.CharField(max_length=10)
                code = ScopedForeignKey(Code, on_delete=models.CASCADE, related_name="+", **given)

                class Meta:
                    app_label = "testapp"

        assert str(raised.value) == message

    def test_inherited_from_an_abstract_base_once(self):
        with isolate_apps("tests.testapp"):

            class Keyed(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                attempt = ScopedForeignKey(Attempt, on_delete=models.CASCADE, related_name="+")

                class Meta:
                    abstract = True

            class Concrete(Keyed):
                class Meta:
                    app_label = "testapp"

        assert [field.name for field in Concrete._meta.local_fields].count("attempt_id") == 1
        assert {each.name for each in Concrete._meta.constraints} == {
            "testapp_concrete_in_scope",
            "testapp_concrete_attempt_in_scope",
        }

    def test_a_migrations_model_takes_the_columns_it_declares(self):
        # A migration's state rebuilds a model from its fields - the column, then the key naming its scope.
        with isolate_apps("tests.testapp"):

            class Rebuilt(models.Model):
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                attempt_id = models.UUIDField(verbose_name="kept")
                attempt = ScopedForeignKey(Attempt, on_delete=models.CASCADE, scope="campaign", related_name="+")

                class Meta:
                    app_label = "testapp"

        assert str(Rebuilt._meta.get_field("attempt_id").verbose_name) == "kept"
        assert Rebuilt._meta.constraints == []


class TestTheRulesEachModelGets:
    def test_a_scoped_model_is_unique_on_its_scope_and_id(self):
        unique = constraint(Attempt, "testapp_attempt_in_scope")

        assert isinstance(unique, models.UniqueConstraint)
        assert unique.fields == ("campaign", "id")
        assert str(unique.violation_error_message) == "A row's id is in its scope once."
        assert Attempt._meta.original_attrs["constraints"] == Attempt._meta.constraints

    def test_each_key_its_constraint_and_index(self):
        key = constraint(AttemptResponse, "testapp_attemptresponse_attempt_in_scope")

        assert (key.fields, key.to, key.to_fields, key.deferrable) == (
            ("campaign", "attempt_id"),
            "testapp.Attempt",
            ("campaign", "id"),
            models.Deferrable.DEFERRED,
        )
        assert str(key.violation_error_message) == "The attempt is in another scope, or isn't there."
        assert str(
            constraint(AttemptResponse, "testapp_attemptresponse_follow_up_in_scope").violation_error_message
        ) == ("The follow up is in another scope, or isn't there.")
        assert [index.fields for index in AttemptResponse._meta.indexes] == [
            ["campaign", "attempt_id"],
            ["campaign", "follow_up_id"],
        ]
        assert AttemptResponse._meta.original_attrs["indexes"] == AttemptResponse._meta.indexes

    @pytest.mark.parametrize(
        ("target", "label"),
        [("Attempt", "testapp.Attempt"), ("other.Thing", "other.Thing"), ("self", "testapp.AttemptResponse")],
    )
    def test_a_targets_label(self, target, label):
        assert scopes._label(AttemptResponse, target) == label

    def test_a_target_model_by_its_label(self):
        assert scopes._label(AttemptResponse, Attempt) == "testapp.Attempt"

    def test_none_for_what_isnt_a_concrete_scoped_model(self):
        for model in (
            types.SimpleNamespace(_meta=types.SimpleNamespace(abstract=True, proxy=False)),
            types.SimpleNamespace(_meta=types.SimpleNamespace(abstract=False, proxy=True)),
        ):
            scopes._hold_rows_in_scope(model)
        assert scopes._hold_rows_in_scope(Campaign) is None
        assert [each.name for each in Campaign._meta.constraints] == []

    def test_none_for_a_scoped_model_naming_no_scope(self):
        with isolate_apps("tests.testapp"):

            class Unnamed(ScopedModel):
                class Meta:
                    app_label = "testapp"

        assert Unnamed._meta.constraints == []


class TestValidation:
    def test_a_target_in_the_scope(self, north, knock):
        response = AttemptResponse(campaign=north, attempt=knock, answer="yes")

        constraint(AttemptResponse, "testapp_attemptresponse_attempt_in_scope").validate(AttemptResponse, response)

    def test_a_target_in_another_scope(self, south, knock):
        response = AttemptResponse(campaign=south, attempt_id=knock.pk, answer="yes")
        key = constraint(AttemptResponse, "testapp_attemptresponse_attempt_in_scope")

        with pytest.raises(ValidationError) as refused:
            key.validate(AttemptResponse, response)

        assert refused.value.messages == ["The attempt is in another scope, or isn't there."]

    def test_a_target_not_there(self, north):
        response = AttemptResponse(campaign=north, attempt_id=uuid.uuid4(), answer="yes")

        with pytest.raises(ValidationError):
            constraint(AttemptResponse, "testapp_attemptresponse_attempt_in_scope").validate(AttemptResponse, response)

    def test_no_target(self, north):
        response = AttemptResponse(campaign=north, answer="yes")

        constraint(AttemptResponse, "testapp_attemptresponse_follow_up_in_scope").validate(AttemptResponse, response)

    def test_a_field_left_out(self, south, knock):
        response = AttemptResponse(campaign=south, attempt_id=knock.pk, answer="yes")
        key = constraint(AttemptResponse, "testapp_attemptresponse_attempt_in_scope")

        key.validate(AttemptResponse, response, exclude={"attempt_id"})
        key.validate(AttemptResponse, response, exclude={"campaign"})
        with pytest.raises(ValidationError):
            key.validate(AttemptResponse, response, exclude={"answer"})

    def test_clean_fields_leaves_the_key_to_its_constraint(self, south, knock):
        response = AttemptResponse(campaign=south, attempt_id=knock.pk, answer="yes")

        response.clean_fields()
        response.clean_fields(exclude=["answer"])

    def test_full_clean(self, south, knock):
        response = AttemptResponse(campaign=south, attempt_id=knock.pk, answer="yes")

        with pytest.raises(ValidationError) as refused:
            response.full_clean()

        assert refused.value.message_dict["__all__"] == ["The attempt is in another scope, or isn't there."]


class TestTheConstraint:
    def make(self, **changes):
        return ScopedForeignKeyConstraint(
            **{
                "name": "c",
                "fields": ["campaign", "attempt_id"],
                "to": "testapp.Attempt",
                "to_fields": ["campaign", "id"],
                **changes,
            }
        )

    def test_deconstructed(self):
        assert self.make().deconstruct() == (
            "isik.django.apps.common.db.scopes.ScopedForeignKeyConstraint",
            (),
            {
                "name": "c",
                "fields": ("campaign", "attempt_id"),
                "to": "testapp.Attempt",
                "to_fields": ("campaign", "id"),
            },
        )
        assert self.make(deferrable=models.Deferrable.DEFERRED, violation_error_message="m").deconstruct()[2] == {
            "name": "c",
            "fields": ("campaign", "attempt_id"),
            "to": "testapp.Attempt",
            "to_fields": ("campaign", "id"),
            "deferrable": models.Deferrable.DEFERRED,
            "violation_error_message": "m",
        }

    @pytest.mark.parametrize(
        "change",
        [
            {"name": "d"},
            {"fields": ["campaign", "other_id"]},
            {"to": "testapp.Other"},
            {"to_fields": ["other", "id"]},
            {"deferrable": models.Deferrable.DEFERRED},
            {"violation_error_message": "m"},
            {"violation_error_code": "x"},
        ],
    )
    def test_equal_only_to_the_same(self, change):
        assert self.make() == self.make()
        assert self.make() != self.make(**change)

    def test_not_equal_to_another_kind(self):
        assert self.make() != models.UniqueConstraint(fields=["campaign"], name="c")
        assert self.make().__eq__(object()) is NotImplemented

    def test_repr(self):
        assert repr(self.make()) == (
            "<ScopedForeignKeyConstraint: c ('campaign', 'attempt_id') -> testapp.Attempt ('campaign', 'id')>"
        )

    def test_its_sql(self):
        built = self.make(name="sample_key", deferrable=models.Deferrable.DEFERRED)

        with connection.schema_editor(collect_sql=True) as editor:
            create = str(built.create_sql(AttemptResponse, editor))
            remove = str(built.remove_sql(AttemptResponse, editor))
            assert built.constraint_sql(AttemptResponse, editor) is None
            assert str(editor.deferred_sql[-1]) == create

        assert create == (
            'ALTER TABLE "testapp_attemptresponse" ADD CONSTRAINT "sample_key" FOREIGN KEY ("campaign_id", '
            '"attempt_id") REFERENCES "testapp_attempt" ("campaign_id", "id") DEFERRABLE INITIALLY DEFERRED'
        )
        assert remove == (
            'SET CONSTRAINTS "sample_key" IMMEDIATE; ALTER TABLE "testapp_attemptresponse" DROP CONSTRAINT "sample_key"'
        )

    def test_added_and_removed(self):
        built = self.make(name="sample_key_again")

        with connection.schema_editor() as editor:
            editor.add_constraint(AttemptResponse, built)
        with connection.cursor() as cursor:
            assert "sample_key_again" in connection.introspection.get_constraints(cursor, "testapp_attemptresponse")
        with connection.schema_editor() as editor:
            editor.remove_constraint(AttemptResponse, built)
        with connection.cursor() as cursor:
            assert "sample_key_again" not in connection.introspection.get_constraints(cursor, "testapp_attemptresponse")


class TestTheCheck:
    @staticmethod
    def problems(*models_):
        return scoped_key_problems([types.SimpleNamespace(get_models=lambda: list(models_))])

    def test_the_test_projects_scoped_models(self):
        assert self.problems(Attempt, AttemptResponse, Campaign, Code, CodedAttempt) == []

    @staticmethod
    def keyed_to(*target_meta, uuid_kind=False, **key):
        """A model keying to a `Target` whose Meta holds `target_meta` - `(name, value)` pairs."""
        with isolate_apps("tests.testapp"):

            class Target(ScopedModel):
                id = models.UUIDField(primary_key=True)
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                kind = models.UUIDField() if uuid_kind else models.CharField(max_length=10)

                Meta = type("Meta", (), {"app_label": "testapp", **dict(target_meta)})

            class Keyed(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                kind = models.CharField(max_length=10)
                target = ScopedForeignKey(Target, on_delete=models.CASCADE, related_name="+", **key)

                class Meta:
                    app_label = "testapp"

        return Keyed

    def test_a_key_to_columns_no_unique_constraint_holds(self):
        keyed = self.keyed_to(to_fields=["id", "kind"])

        assert self.problems(keyed) == [
            "testapp.Keyed.target references testapp.Target's (campaign, id, kind), which no unique constraint of "
            "testapp.Target holds - add models.UniqueConstraint(fields=['campaign', 'id', 'kind'], "
            "name='testapp_target_id_kind_in_scope') to its Meta.constraints."
        ]

    @pytest.mark.parametrize(
        "meta",
        [
            ("unique_together", [("kind", "id", "campaign")]),
            ("constraints", [models.UniqueConstraint(fields=["kind", "campaign", "id"], name="held")]),
        ],
    )
    def test_a_key_to_columns_a_unique_constraint_holds(self, meta):
        assert self.problems(self.keyed_to(meta, to_fields=["id", "kind"])) == []

    @pytest.mark.parametrize(
        "unique",
        [
            models.UniqueConstraint(
                fields=["campaign", "id", "kind"], name="late", deferrable=models.Deferrable.DEFERRED
            ),
            models.UniqueConstraint(fields=["campaign", "id", "kind"], name="some", condition=models.Q(kind="a")),
            models.UniqueConstraint(models.F("campaign"), models.F("id"), models.F("kind"), name="expressed"),
            models.UniqueConstraint(fields=["campaign", "id"], name="narrower"),
        ],
    )
    def test_a_unique_constraint_postgres_cant_reference(self, unique):
        problems = self.problems(self.keyed_to(("constraints", [unique]), to_fields=["id", "kind"]))

        assert [problem.split(" holds - ")[0] for problem in problems] == [
            "testapp.Keyed.target references testapp.Target's (campaign, id, kind), which no unique constraint of "
            "testapp.Target"
        ]

    def test_a_key_naming_fields_that_arent_there(self):
        keyed = self.keyed_to(to_fields=["id", "sort"], from_fields=["target_id", "order"])

        assert self.problems(keyed) == [
            "testapp.Keyed.target names order, sort, which neither it nor testapp.Target has."
        ]

    def test_a_key_holding_something_else_in_its_column(self):
        keyed = self.keyed_to(to_fields=["kind"])

        assert self.problems(keyed) == [
            "testapp.Keyed.target holds testapp.Target.kind in its UUID column, and it isn't a UUID."
        ]

    def test_a_key_holding_another_uuid_in_its_column(self):
        keyed = self.keyed_to(("unique_together", [("campaign", "kind")]), uuid_kind=True, to_fields=["kind"])

        assert self.problems(keyed) == []

    def test_a_plain_key_between_two_models_in_one_scope(self):
        with isolate_apps("tests.testapp"):

            class Plain(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="+")
                one = models.OneToOneField(Attempt, on_delete=models.CASCADE, related_name="+")

                class Meta:
                    app_label = "testapp"

        assert self.problems(Plain) == [
            "testapp.Plain.attempt is a plain key between two models scoped by campaign - declare it a "
            "ScopedForeignKey, so the database keeps both in one scope.",
            "testapp.Plain.one is a plain key between two models scoped by campaign - declare it a "
            "ScopedForeignKey, so the database keeps both in one scope.",
        ]

    def test_a_plain_key_to_a_model_in_another_scope_or_none(self):
        with isolate_apps("tests.testapp"):

            class Region(ScopedModel):
                scope_field = "area"
                area = models.CharField(max_length=10)

                class Meta:
                    app_label = "testapp"

            class Keys(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="+")
                pending = models.ForeignKey("testapp.Nowhere", on_delete=models.CASCADE, related_name="+")

                class Meta:
                    app_label = "testapp"

        assert self.problems(Keys) == []

    def test_a_scoped_key_to_a_model_in_another_scope(self):
        with isolate_apps("tests.testapp"):

            class Region(ScopedModel):
                scope_field = "area"
                area = models.CharField(max_length=10)

                class Meta:
                    app_label = "testapp"

            class Keyed(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                region = ScopedForeignKey(Region, on_delete=models.CASCADE, related_name="+")
                plain = ScopedForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")

                class Meta:
                    app_label = "testapp"

        assert self.problems(Keyed) == [
            "testapp.Keyed.region keys to testapp.Region, which isn't scoped by campaign - scope it by the same field.",
            "testapp.Keyed.plain keys to testapp.Campaign, which isn't scoped by campaign - scope it by the same "
            "field.",
        ]

    def test_a_scoped_key_to_a_model_keyed_by_something_else(self):
        with isolate_apps("tests.testapp"):

            class Counted(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")

                class Meta:
                    app_label = "testapp"

            class Keyed(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                counted = ScopedForeignKey(Counted, on_delete=models.CASCADE, related_name="+")

                class Meta:
                    app_label = "testapp"

        assert self.problems(Keyed) == [
            "testapp.Keyed.counted keys to testapp.Counted, whose id isn't a UUID - key it by new_primary_key()."
        ]

    def test_a_scoped_model_naming_no_scope(self):
        with isolate_apps("tests.testapp"):

            class Unnamed(ScopedModel):
                class Meta:
                    app_label = "testapp"

            class Misnamed(ScopedModel):
                scope_field = "campain"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")

                class Meta:
                    app_label = "testapp"

        assert self.problems(Unnamed, Misnamed) == [
            "testapp.Unnamed is a ScopedModel naming no scope_field - name the field its scope is.",
            "testapp.Misnamed's scope_field is 'campain', which isn't one of its fields.",
        ]

    def test_a_proxy_and_an_unscoped_model_ask_nothing(self):
        with isolate_apps("tests.testapp"):

            class Proxied(Attempt):
                class Meta:
                    app_label = "testapp"
                    proxy = True

        assert self.problems(Proxied, Campaign) == []

    def test_registered(self, monkeypatch):
        from isik.django.apps.common import checks

        seen = []
        monkeypatch.setattr(scopes, "scoped_key_problems", lambda configs: seen.append(configs) or ["one"])

        errors = checks.every_key_in_a_scope_stays_in_it(app_configs=None)

        assert [(error.id, error.msg, error.hint) for error in errors] == [
            ("isik.E025", "one", "See isik.django.apps.common.db.scopes.")
        ]
        assert [config.label for config in seen[0]] == ["testapp"]


class TestAKeyBuiltNow:
    """Keys declared while the test runs - so what the constructor and contribute_to_class do is what's checked."""

    @staticmethod
    def build(**options):
        with isolate_apps("tests.testapp"):

            class Built(ScopedModel):
                scope_field = "campaign"
                campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="+")
                order_line = ScopedForeignKey(Attempt, related_name="lines", **options)

                class Meta:
                    app_label = "testapp"

        return Built, Built._meta.get_field("order_line"), Built._meta.get_field("order_line_id")

    def test_to_fields_name_its_own_fields_alike(self):
        model, key, column = self.build(on_delete=models.CASCADE, to_fields=("id", "door"))

        assert (key.from_fields, key.to_fields) == (["campaign", "order_line_id", "door"], ["campaign", "id", "door"])

    def test_its_column(self):
        model, key, column = self.build(on_delete=models.CASCADE, help_text="The line.", db_comment="A line.")

        assert isinstance(column, models.UUIDField)
        assert (column.null, column.blank, column.editable) == (False, False, False)
        assert (str(column.verbose_name), column._verbose_name is column.verbose_name) == ("order line id", True)
        assert (column.help_text, column.db_comment) == ("The line.", "A line.")
        assert (key.help_text, key.db_comment) == ("The line.", None)

    def test_a_named_key_names_its_column(self):
        model, key, column = self.build(on_delete=models.CASCADE, verbose_name="line ordered")

        assert str(column.verbose_name) == "line ordered id"

    def test_nullable(self):
        model, key, column = self.build(on_delete=models.SET_NULL, null=True)

        assert (key.null, key.blank, column.null, column.blank) == (True, True, True, True)
        assert key.remote_field.on_delete is scopes._set_null_column
        assert key.deconstruct()[3]["on_delete"] is models.SET_NULL

    def test_its_relation(self):
        model, key, column = self.build(on_delete=models.CASCADE, deferrable=models.Deferrable.DEFERRED)

        assert (key.from_fields, key.to_fields) == (["campaign", "order_line_id"], ["campaign", None])
        assert (key.remote_field.related_name, key.deferrable, key.editable) == (
            "lines",
            models.Deferrable.DEFERRED,
            False,
        )
        assert key.remote_field.on_delete is models.CASCADE
        assert key.deconstruct()[3]["deferrable"] is models.Deferrable.DEFERRED
        assert "editable" not in key.deconstruct()[3]

    def test_made_editable(self):
        model, key, column = self.build(on_delete=models.CASCADE, editable=True)

        assert key.editable is True
        assert key.deconstruct()[3]["editable"] is True

    def test_its_rules(self):
        model, key, column = self.build(on_delete=models.CASCADE)

        assert [each.name for each in model._meta.constraints] == [
            "testapp_built_in_scope",
            "testapp_built_order_line_in_scope",
        ]
        assert [index.fields for index in model._meta.indexes] == [["campaign", "order_line_id"]]


class TestTheConstraintsReferences:
    def test_its_sql_names_both_tables_and_their_columns(self):
        built = constraint(AttemptResponse, "testapp_attemptresponse_attempt_in_scope")

        with connection.schema_editor(collect_sql=True) as editor:
            statement = built.create_sql(AttemptResponse, editor)

        assert statement.references_table("testapp_attemptresponse")
        assert statement.references_table("testapp_attempt")
        assert statement.references_column("testapp_attemptresponse", "attempt_id")
        assert statement.references_column("testapp_attempt", "id")

    def test_validate_reads_the_database_it_is_given(self, north, knock):
        from django.utils.connection import ConnectionDoesNotExist

        response = AttemptResponse(campaign=north, attempt=knock, answer="yes")

        with pytest.raises(ConnectionDoesNotExist):
            constraint(AttemptResponse, "testapp_attemptresponse_attempt_in_scope").validate(
                AttemptResponse, response, using="elsewhere"
            )

    def test_its_violation_code(self, south, knock):
        coded = ScopedForeignKeyConstraint(
            name="c",
            fields=["campaign", "attempt_id"],
            to="testapp.Attempt",
            to_fields=["campaign", "id"],
            violation_error_code="out_of_scope",
        )

        with pytest.raises(ValidationError) as refused:
            coded.validate(AttemptResponse, AttemptResponse(campaign=south, attempt_id=knock.pk, answer="yes"))

        assert refused.value.code == "out_of_scope"

    def test_a_scoped_model_after_an_unscoped_one_is_checked(self):
        with isolate_apps("tests.testapp"):

            class Unnamed(ScopedModel):
                class Meta:
                    app_label = "testapp"

        assert scoped_key_problems([types.SimpleNamespace(get_models=lambda: [Campaign, Unnamed])]) == [
            "testapp.Unnamed is a ScopedModel naming no scope_field - name the field its scope is."
        ]


class TestAKeyToAnyUnique:
    """`CodedAttempt.code` references `Code`'s `(campaign, id, kind)` - the scope and the kind kept with the key."""

    @pytest.fixture
    def door(self, north):
        return Code.objects.create(campaign=north, kind="door")

    @pytest.fixture
    def call(self, north):
        return Code.objects.create(campaign=north, kind="call")

    def test_its_related_fields(self):
        code, follow = CodedAttempt._meta.get_field("code"), CodedAttempt._meta.get_field("follow_code")

        assert (code.from_fields, code.to_fields) == (["campaign", "code_id", "kind"], ["campaign", "id", "kind"])
        assert (code.referencing, code.referenced) == (("code_id", "kind"), ("id", "kind"))
        assert (follow.from_fields, follow.to_fields) == (
            ["campaign", "follow_code_id", "follow_kind"],
            ["campaign", "id", "kind"],
        )
        assert (follow.referencing, follow.referenced) == (("follow_code_id", "follow_kind"), ("id", "kind"))
        default = AttemptResponse._meta.get_field("attempt")
        assert (default.referencing, default.referenced) == (("attempt_id",), ("id",))

    def test_deconstructed(self):
        code = CodedAttempt._meta.get_field("code").deconstruct()[3]
        follow = CodedAttempt._meta.get_field("follow_code").deconstruct()[3]

        assert (code["to_fields"], "from_fields" in code) == (("id", "kind"), False)
        assert (follow["to_fields"], follow["from_fields"]) == (("id", "kind"), ("follow_code_id", "follow_kind"))

    def test_its_constraint_and_index(self):
        key = constraint(CodedAttempt, "testapp_codedattempt_code_in_scope")
        index = next(each for each in CodedAttempt._meta.indexes if each.name == "testapp_02aff938_code_in_scope")

        assert (key.fields, key.to, key.to_fields) == (
            ("campaign", "code_id", "kind"),
            "testapp.Code",
            ("campaign", "id", "kind"),
        )
        assert index.fields == ["campaign", "code_id", "kind"]

    def test_assigning_the_code_sets_its_scope_and_kind(self, north, door):
        attempt = CodedAttempt(code=door)

        assert (attempt.campaign_id, attempt.code_id, attempt.kind) == (north.pk, door.pk, "door")
        attempt.follow_code = door
        assert (attempt.follow_code_id, attempt.follow_kind) == (door.pk, "door")

    def test_a_kind_the_code_doesnt_have_is_refused(self, north, door):
        attempt = CodedAttempt.objects.create(code=door)

        with pytest.raises(IntegrityError), transaction.atomic():
            CodedAttempt.objects.filter(pk=attempt.pk).update(kind="call")
        with pytest.raises(IntegrityError), transaction.atomic():
            Code.objects.filter(pk=door.pk).update(kind="call")

    def test_full_clean_refuses_a_kind_the_code_doesnt_have(self, north, door):
        attempt = CodedAttempt(code=door)
        attempt.kind = "call"

        with pytest.raises(ValidationError) as refused:
            attempt.full_clean()

        assert refused.value.messages == ["The code is in another scope, or isn't there."]

    def test_traversal(self, north, door, call):
        attempt = CodedAttempt.objects.create(code=door, follow_code=call)

        assert list(door.attempts.all()) == [attempt]
        assert CodedAttempt.objects.select_related("code").get(code=door).code == door
        assert list(CodedAttempt.objects.filter(follow_code=call)) == [attempt]

    def test_set_null_nulls_its_column(self, north, door, call):
        attempt = CodedAttempt.objects.create(code=door, follow_code=call)

        call.delete()

        attempt.refresh_from_db()
        assert (attempt.follow_code_id, attempt.follow_kind, attempt.follow_code) == (None, "call", None)

    def test_refreshing_an_extra_column_forgets_the_row(self, north, door):
        attempt = CodedAttempt.objects.create(code=door)
        assert attempt.code == door

        attempt.refresh_from_db(fields=["kind"])

        assert "code" not in attempt._state.fields_cache

    def test_postgres_holds_it(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, CodedAttempt._meta.db_table)

        assert constraints["testapp_codedattempt_code_in_scope"]["columns"] == ["campaign_id", "code_id", "kind"]
        assert constraints["testapp_codedattempt_code_in_scope"]["foreign_key"] == ("testapp_code", "campaign_id")
