import datetime
import sys
import uuid
from pathlib import Path

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.test.utils import isolate_apps

from isik.common.utils.exemptions import declared_exemptions
from isik.django.apps.common.db import DatabaseTimestampsModel, VersionedModel, concurrency
from isik.django.apps.common.db.concurrency import (
    ConcurrencyToken,
    UnprovenConcurrencyToken,
    assert_concurrency_field_changes,
    concurrency_attname,
    concurrency_etag,
    concurrency_field_name,
    concurrency_token_problems,
    current_concurrency_etag,
)
from tests.testapp.models import (
    CleanedNote,
    RevisionedNote,
    StuckNote,
    TimestampedNote,
    VersionedNote,
    VersionedWidget,
    Widget,
)


# Where this module's code says it is - under mutmut, a copied test's cached bytecode can name the
# original file rather than __file__.
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())
REASON = "moved on by one by a trigger of the project's own on every write"


def isolated_model(base, **attributes):
    # Uniquely named: a DatabaseTimestampsModel or VersionedModel registers its triggers process-wide,
    # keyed by table, which a second run of the same test in one process would collide with.
    meta = type("Meta", (), {"app_label": "testapp"})
    name = f"Token{uuid.uuid4().hex[:8]}"
    return type(name, (base,), {"__module__": __name__, "Meta": meta, **attributes})


class TestWhichFieldIsTheToken:
    def test_a_versioned_model_uses_its_version_even_with_the_timestamps(self):
        assert concurrency_field_name(VersionedWidget) == "version"
        assert concurrency_field_name(VersionedNote) == "version"

    def test_a_model_with_the_timestamps_uses_updated_at(self):
        assert concurrency_field_name(Widget) == "updated_at"
        assert concurrency_field_name(TimestampedNote) == "updated_at"

    def test_a_declared_token_is_used(self):
        assert concurrency_field_name(RevisionedNote) == "revision"

    @isolate_apps("tests.testapp")
    def test_a_named_field_is_used_over_either_default(self):
        model = isolated_model(VersionedModel, concurrency_field="text", text=models.IntegerField())

        assert concurrency_field_name(model) == "text"

    def test_a_model_with_neither_has_none(self):
        assert concurrency_field_name(CleanedNote) is None

    def test_the_attribute_is_the_columns_for_a_foreign_key(self):
        assert concurrency_attname(Widget) == "updated_at"


class TestConcurrencyToken:
    def test_names_the_field_and_keeps_its_reason_as_an_exemption(self):
        token, line = ConcurrencyToken("revision", reason=REASON), sys._getframe().f_lineno

        assert token.field == "revision"
        assert isinstance(token.reason, UnprovenConcurrencyToken)
        assert token.reason == REASON
        made = declared_exemptions("isik.concurrency-token.unproven")[-1]
        assert made is token.reason
        assert (made.file, made.line) == (HERE, line)

    def test_takes_a_fields_name(self):
        with pytest.raises(TypeError) as raised:
            ConcurrencyToken(models.IntegerField(), reason=REASON)

        assert str(raised.value).startswith("ConcurrencyToken takes the field's name, not <django.db.models.fields")

    def test_a_short_reason_is_refused(self):
        with pytest.raises(ValueError, match=r"^UnprovenConcurrencyToken needs a reason of at least 40 characters"):
            ConcurrencyToken("revision", reason="a trigger")


class TestProblems:
    @pytest.mark.parametrize("model", [Widget, VersionedWidget, VersionedNote, RevisionedNote, StuckNote])
    def test_a_token_isik_keeps_or_one_declared_has_none(self, model):
        assert concurrency_token_problems(model) == []

    def test_a_model_without_one_is_told_how_to_get_one(self):
        assert concurrency_token_problems(CleanedNote) == [
            "testapp.CleanedNote has no concurrency token - make it a VersionedModel or a DatabaseTimestampsModel, "
            "or name one with concurrency_field = ConcurrencyToken(field, reason=...)."
        ]

    @isolate_apps("tests.testapp")
    def test_a_name_that_isnt_a_field(self):
        model = isolated_model(models.Model, concurrency_field=ConcurrencyToken("missing", reason=REASON))

        assert concurrency_token_problems(model) == [
            f"testapp.{model.__name__}'s concurrency token 'missing' is not one of its fields."
        ]

    @isolate_apps("tests.testapp")
    def test_a_field_that_isnt_a_column(self):
        model = isolated_model(
            models.Model,
            concurrency_field=ConcurrencyToken("target", reason=REASON),
            content_type=models.ForeignKey(ContentType, on_delete=models.CASCADE),
            object_id=models.PositiveIntegerField(),
            target=GenericForeignKey(),
        )

        assert concurrency_token_problems(model) == [
            f"testapp.{model.__name__}'s concurrency token 'target' is not a column."
        ]

    @isolate_apps("tests.testapp")
    def test_a_column_that_can_be_null(self):
        model = isolated_model(
            models.Model,
            concurrency_field=ConcurrencyToken("revision", reason=REASON),
            revision=models.IntegerField(null=True),
        )

        assert concurrency_token_problems(model) == [
            f"testapp.{model.__name__}'s concurrency token 'revision' can be null."
        ]

    @isolate_apps("tests.testapp")
    @pytest.mark.parametrize(
        "field", [models.BigIntegerField(), models.PositiveIntegerField(), models.DateTimeField(), models.UUIDField()]
    )
    def test_an_integer_a_datetime_or_a_uuid_can_be_one(self, field):
        model = isolated_model(
            models.Model, concurrency_field=ConcurrencyToken("revision", reason=REASON), revision=field
        )

        assert concurrency_token_problems(model) == []

    @isolate_apps("tests.testapp")
    @pytest.mark.parametrize("field", [models.CharField(max_length=10), models.DateField(), models.FloatField()])
    def test_anything_else_cant(self, field):
        model = isolated_model(
            models.Model, concurrency_field=ConcurrencyToken("revision", reason=REASON), revision=field
        )

        assert concurrency_token_problems(model) == [
            f"testapp.{model.__name__}'s concurrency token 'revision' must be an integer, a datetime or a UUID."
        ]

    @isolate_apps("tests.testapp")
    def test_a_field_named_but_not_declared_must_be_declared(self):
        model = isolated_model(models.Model, concurrency_field="revision", revision=models.IntegerField())

        assert concurrency_token_problems(model) == [
            f"testapp.{model.__name__}'s concurrency token 'revision' isn't kept by one of isik's triggers - "
            "declare it with ConcurrencyToken('revision', reason=...), saying why it changes on every write."
        ]

    @isolate_apps("tests.testapp")
    def test_naming_the_field_isiks_trigger_keeps_is_enough(self):
        stamped = isolated_model(DatabaseTimestampsModel, concurrency_field="updated_at")
        versioned = isolated_model(VersionedModel, concurrency_field="version")

        assert concurrency_token_problems(stamped) == concurrency_token_problems(versioned) == []

    @isolate_apps("tests.testapp")
    def test_a_field_named_like_isiks_but_kept_by_neither_trigger_must_be_declared(self):
        # Each of the two triggers keeps only its own field.
        stamped = isolated_model(DatabaseTimestampsModel, concurrency_field="version", version=models.IntegerField())
        versioned = isolated_model(
            VersionedModel, concurrency_field="updated_at", updated_at=models.DateTimeField(auto_now=True)
        )

        assert [len(concurrency_token_problems(model)) for model in (stamped, versioned)] == [1, 1]
        assert "isn't kept by one of isik's triggers" in concurrency_token_problems(stamped)[0]
        assert "isn't kept by one of isik's triggers" in concurrency_token_problems(versioned)[0]


class TestTheETag:
    def test_a_datetime_is_its_microseconds_since_the_epoch(self):
        moment = datetime.datetime(2026, 10, 21, 12, 30, 15, 123456, tzinfo=datetime.UTC)

        assert concurrency_etag(Widget(updated_at=moment)) == '"1792585815123456"'

    def test_the_same_moment_in_another_zone_is_the_same_tag(self):
        moment = datetime.datetime(
            2026, 10, 21, 15, 30, 15, 123456, tzinfo=datetime.timezone(datetime.timedelta(hours=3))
        )

        assert concurrency_etag(Widget(updated_at=moment)) == '"1792585815123456"'

    def test_a_version_is_its_number(self):
        assert concurrency_etag(VersionedNote(version=7)) == '"7"'

    @isolate_apps("tests.testapp")
    def test_a_uuid_is_its_hex(self):
        model = isolated_model(
            models.Model, concurrency_field=ConcurrencyToken("revision", reason=REASON), revision=models.UUIDField()
        )
        token = uuid.UUID("12345678-1234-5678-1234-567812345678")

        assert concurrency_etag(model(revision=token)) == '"12345678123456781234567812345678"'

    @pytest.mark.django_db
    def test_the_current_one_is_read_from_the_database(self):
        note = VersionedNote.objects.create(text="a")
        VersionedNote.objects.filter(pk=note.pk).update(text="b")

        assert current_concurrency_etag(VersionedNote, note.pk) == '"2"'
        assert concurrency_etag(note) == '"1"'

    @pytest.mark.django_db
    def test_a_row_that_isnt_there_has_none(self):
        assert current_concurrency_etag(VersionedNote, 0) is None


class TestTheTokensTheHelperWrites:
    def test_an_older_one(self):
        moment = datetime.datetime(2026, 10, 27, tzinfo=datetime.UTC)

        assert concurrency._older(5) == 4
        assert concurrency._older(moment) == moment - datetime.timedelta(days=1)

    def test_a_newer_one_past_one_step(self):
        moment = datetime.datetime(2026, 10, 27, tzinfo=datetime.UTC)

        assert concurrency._newer(5) == 7
        assert concurrency._newer(moment) == moment + datetime.timedelta(days=1)

    @pytest.mark.parametrize("make", ["_older", "_newer"])
    def test_any_other_uuid(self, make):
        token = uuid.uuid4()

        made = getattr(concurrency, make)(token)

        assert isinstance(made, uuid.UUID)
        assert made != token


@pytest.mark.django_db
class TestAssertConcurrencyFieldChanges:
    @pytest.mark.parametrize("model", [Widget, VersionedWidget, VersionedNote, RevisionedNote])
    def test_a_token_that_changes_on_every_write_passes(self, model):
        instance = model._default_manager.create(**({"name": "a"} if hasattr(model, "name") else {}))

        assert_concurrency_field_changes(instance)

    def test_only_the_writes_naming_a_newer_token_move_isiks(self):
        note = VersionedNote.objects.create(text="a")

        assert_concurrency_field_changes(note)

        note.refresh_from_db()
        assert note.version == 3

    def test_a_token_nothing_keeps_is_caught_taking_the_older_one(self):
        note = StuckNote.objects.create(text="a")

        with pytest.raises(AssertionError) as raised:
            assert_concurrency_field_changes(note)

        assert str(raised.value) == (
            "testapp.StuckNote's concurrency token 'revision' was 0 after QuerySet.update() naming an older token."
        )

    def test_a_token_only_save_moves_on_is_caught_going_back(self, monkeypatch):
        def save(self, *args, **kwargs):
            self.revision += 1
            models.Model.save(self, *args, **kwargs)

        monkeypatch.setattr(StuckNote, "save", save)
        note = StuckNote.objects.create(text="a")

        with pytest.raises(AssertionError) as raised:
            assert_concurrency_field_changes(note)

        assert str(raised.value) == (
            "testapp.StuckNote's concurrency token 'revision' was 1 after QuerySet.update() naming an older token."
        )

    def test_a_newer_token_that_doesnt_move_it_is_caught(self, monkeypatch):
        # Named as older than it is, isik's trigger holds it where it was.
        monkeypatch.setattr(concurrency, "_newer", lambda token: token - 1)
        note = VersionedNote.objects.create(text="a")

        with pytest.raises(AssertionError) as raised:
            assert_concurrency_field_changes(note)

        assert str(raised.value) == (
            "testapp.VersionedNote's concurrency token 'version' was 1 after QuerySet.update() naming a newer token."
        )

    def test_a_newer_token_taken_as_named_is_caught(self, monkeypatch):
        # One step on is where isik's trigger puts it anyway - _newer() names two for that.
        monkeypatch.setattr(concurrency, "_newer", lambda token: token + 1)
        note = VersionedNote.objects.create(text="a")

        with pytest.raises(AssertionError) as raised:
            assert_concurrency_field_changes(note)

        assert str(raised.value) == (
            "testapp.VersionedNote's concurrency token 'version' was 2 after QuerySet.update() naming a newer token."
        )

    def test_a_model_without_a_token_is_refused(self):
        note = CleanedNote.objects.create(count=1)

        with pytest.raises(AssertionError) as raised:
            assert_concurrency_field_changes(note)

        assert str(raised.value) == "testapp.CleanedNote has no concurrency token."
