import datetime
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pgtrigger
import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, connections, models
from django.db.models import F
from django.db.models.functions import Upper
from django.test.utils import isolate_apps
from django.utils import timezone
from django_lifecycle import AFTER_DELETE, BEFORE_DELETE, bypass_hooks_for

from isik.django.apps.common.db import (
    BaseModel,
    DatabaseTimestampsModel,
    FullCleanOnSaveModel,
    ReprModel,
    UUIDPrimaryKeyModel,
    VersionedModel,
    event_model_for,
)
from isik.django.apps.common.db import models as base_models
from tests.testapp.models import (
    CleanedNote,
    ModifiedColumnNote,
    Recorder,
    TimestampedNote,
    VersionedNote,
    VersionedWidget,
    Widget,
    WidgetProxy,
)


pytestmark = pytest.mark.django_db


def test_check_pgtrigger_installed_raises_when_pgtrigger_is_not_in_installed_apps(monkeypatch):
    monkeypatch.setattr(base_models.django_apps, "is_installed", lambda app_name: False)
    with pytest.raises(ImproperlyConfigured) as exc_info:
        base_models._check_pgtrigger_installed()
    assert str(exc_info.value) == (
        "DatabaseTimestampsModel (and so BaseModel) requires 'pgtrigger' in INSTALLED_APPS - it "
        "maintains created_at/updated_at via database triggers, not Django's auto_now/auto_now_add. "
        "django-pgtrigger installs automatically as django-pghistory's dependency; add both to INSTALLED_APPS."
    )


def test_check_pgtrigger_installed_is_a_noop_when_pgtrigger_is_installed():
    base_models._check_pgtrigger_installed()  # pgtrigger is genuinely installed in tests - no raise


def test_timestamp_triggers_protects_created_at_and_stamps_updated_at():
    protect_created_at, stamp_updated_at = base_models._timestamp_triggers(TimestampedNote)

    assert protect_created_at.name == "protect_created_at"
    assert protect_created_at.fields == ["created_at"]

    assert stamp_updated_at.name == "stamp_updated_at"
    assert stamp_updated_at.when == pgtrigger.Before
    assert stamp_updated_at.operation == pgtrigger.Update
    assert stamp_updated_at.get_func(TimestampedNote) == (
        'IF ((OLD."created_at" IS DISTINCT FROM (NEW."created_at") OR OLD."id" IS DISTINCT FROM (NEW."id") OR '
        'OLD."text" IS DISTINCT FROM (NEW."text"))) OR NEW."updated_at" > OLD."updated_at" THEN '
        'NEW."updated_at" = GREATEST(clock_timestamp(), OLD."updated_at" + interval \'1 microsecond\'); '
        'ELSE NEW."updated_at" = OLD."updated_at"; END IF; RETURN NEW;'
    )


def test_timestamp_triggers_stamp_the_column_updated_at_is_stored_in():
    _, stamp_updated_at = base_models._timestamp_triggers(ModifiedColumnNote)
    assert stamp_updated_at.get_func(ModifiedColumnNote) == (
        'IF ((OLD."created_at" IS DISTINCT FROM (NEW."created_at") OR OLD."id" IS DISTINCT FROM (NEW."id") OR '
        'OLD."text" IS DISTINCT FROM (NEW."text"))) OR NEW."Modified" > OLD."Modified" THEN '
        'NEW."Modified" = GREATEST(clock_timestamp(), OLD."Modified" + interval \'1 microsecond\'); '
        'ELSE NEW."Modified" = OLD."Modified"; END IF; RETURN NEW;'
    )


def test_the_kept_fields_are_named_as_the_trigger_is_made_not_as_it_is_rendered():
    # A migration renders it against a model of its own state - no subclass of DatabaseTimestampsModel.
    _, stamp_updated_at = base_models._timestamp_triggers(TimestampedNote)

    assert stamp_updated_at.kept == ("updated_at",)
    assert base_models._version_triggers(VersionedWidget)[0].kept == ("version", "updated_at")


def test_updated_at_moves_on_by_a_microsecond_when_the_clock_is_behind_it():
    """A clock stepping back, or two writes in one microsecond, mustn't leave it where it was - it's a
    concurrency token."""
    note = TimestampedNote.objects.create(text="a")
    ahead = timezone.now() + datetime.timedelta(days=1)
    with pgtrigger.ignore("testapp.TimestampedNote:stamp_updated_at"):
        TimestampedNote.objects.filter(pk=note.pk).update(updated_at=ahead)

    TimestampedNote.objects.filter(pk=note.pk).update(text="b")
    note.refresh_from_db()

    assert note.updated_at == ahead + datetime.timedelta(microseconds=1)


def test_updated_at_takes_the_clock_when_the_clock_is_ahead():
    note = TimestampedNote.objects.create(text="a")
    behind = timezone.now() - datetime.timedelta(days=1)
    with pgtrigger.ignore("testapp.TimestampedNote:stamp_updated_at"):
        TimestampedNote.objects.filter(pk=note.pk).update(updated_at=behind)

    before = timezone.now()
    TimestampedNote.objects.filter(pk=note.pk).update(text="b")
    note.refresh_from_db()

    assert note.updated_at >= before


@pytest.mark.django_db
def test_an_update_stamps_updated_at_under_its_own_column_name():
    note = ModifiedColumnNote.objects.create(text="a")
    note.refresh_from_db()
    updated = note.updated_at
    ModifiedColumnNote.objects.filter(pk=note.pk).update(text="b")
    note.refresh_from_db()
    assert note.text == "b"
    assert note.updated_at > updated


@pytest.mark.django_db(transaction=True)
def test_updated_at_follows_the_order_of_the_writes_not_of_the_transactions():
    widget = Widget.objects.create(name="a")
    older = connections.create_connection("default")
    try:
        older.set_autocommit(False)
        with older.cursor() as cursor:
            cursor.execute("SELECT 1")  # its transaction starts here
        time.sleep(0.01)
        # A transaction that began later writes and commits first.
        Widget.objects.filter(pk=widget.pk).update(name="b")
        with older.cursor() as cursor:
            cursor.execute(f"UPDATE {Widget._meta.db_table} SET name = 'c' WHERE id = %s", [widget.pk])
        older.commit()
    finally:
        older.close()

    stamps = list(
        event_model_for(Widget).objects.filter(pgh_obj=widget).order_by("pgh_id").values_list("name", "updated_at")
    )
    assert [name for name, _ in stamps] == ["a", "b", "c"]
    assert stamps[0][1] < stamps[1][1] < stamps[2][1]


def test_timestamp_triggers_are_registered_on_every_concrete_basemodel_subclass():
    trigger_names = {trigger.name for trigger in Widget._meta.triggers}
    assert {"protect_created_at", "stamp_updated_at"} <= trigger_names


def test_save_runs_full_clean_and_rejects_invalid_field_values():
    with pytest.raises(ValidationError):
        Widget.objects.create(name="bolt", count=-1)


def test_save_persists_valid_values():
    widget = Widget.objects.create(name="bolt", count=3)
    assert Widget.objects.get(pk=widget.pk).count == 3


def test_skip_full_clean_bypasses_validation_for_the_duration_of_the_block():
    widget = Widget.objects.create(name="bolt", count=1)
    with widget.skip_full_clean():
        widget.count = -5
        widget.save()
    assert Widget.objects.get(pk=widget.pk).count == -5


def test_skip_full_clean_only_applies_inside_the_block():
    widget = Widget.objects.create(name="bolt", count=1)
    with widget.skip_full_clean():
        pass
    widget.count = -5
    with pytest.raises(ValidationError):
        widget.save()


def test_update_sets_attributes_and_persists_them():
    widget = Widget.objects.create(name="bolt", count=1)
    widget.update(count=9)
    assert Widget.objects.get(pk=widget.pk).count == 9


def test_update_passes_only_the_changed_kwargs_as_update_fields():
    widget = Widget.objects.create(name="bolt", count=1)
    with patch.object(Widget, "save", autospec=True, side_effect=Widget.save) as spy:
        widget.update(count=9)
    assert spy.call_args.kwargs["update_fields"] == ["count"]


def test_update_with_skip_hooks_still_runs_full_clean():
    widget = Widget.objects.create(name="bolt", count=1)
    with pytest.raises(ValidationError):
        widget.update(count=-9, _skip_hooks=True)


def test_skip_hooks_combined_with_skip_full_clean_bypasses_validation_too():
    widget = Widget.objects.create(name="bolt", count=1)
    with widget.skip_full_clean():
        widget.update(count=-9, _skip_hooks=True)
    assert Widget.objects.get(pk=widget.pk).count == -9


def test_as_queryset_returns_a_queryset_matching_only_this_object():
    widget = Widget.objects.create(name="bolt", count=1)
    Widget.objects.create(name="nut", count=2)
    assert list(widget.as_queryset()) == [widget]


def test_repr_uses_the_default_format():
    widget = Widget.objects.create(name="bolt", count=1)
    assert repr(widget) == f"Widget(id={widget.id})"


def test_str_falls_back_to_repr_when_str_is_not_set():
    widget = Widget.objects.create(name="bolt", count=1)
    assert str(widget) == repr(widget)


def test_str_uses_the_str_template_when_set():
    recorder = Recorder.objects.create(name="rec-1")
    assert str(recorder) == "Recorder<rec-1>"


class TestLifecycleHookOrdering:
    def test_create_runs_before_create_before_save_then_after_save_after_create(self):
        recorder = Recorder(name="rec-1")
        recorder.save()
        assert recorder.hook_log == ["before_create", "before_save", "after_save", "after_create"]

    def test_update_runs_before_update_before_save_then_after_save_after_update(self):
        recorder = Recorder.objects.create(name="rec-1")
        recorder.hook_log = []
        recorder.name = "rec-2"
        recorder.save()
        assert recorder.hook_log == ["before_update", "before_save", "after_save", "after_update"]

    def test_skip_hooks_runs_no_lifecycle_hooks_at_all(self):
        recorder = Recorder(name="rec-1")
        recorder.save(_skip_hooks=True)
        assert recorder.hook_log == []

    def test_update_skip_hooks_also_skips_lifecycle_hooks(self):
        recorder = Recorder.objects.create(name="rec-1")
        recorder.hook_log = []
        recorder.update(name="rec-2", _skip_hooks=True)
        assert recorder.hook_log == []

    def test_delete_runs_before_delete_then_after_delete(self):
        recorder = Recorder.objects.create(name="rec-1")
        recorder.hook_log = []
        recorder.delete()
        assert recorder.hook_log == ["before_delete", "after_delete"]

    def test_delete_returns_what_the_model_returns(self):
        recorder = Recorder.objects.create(name="rec-1")
        deleted, _per_model = recorder.delete()
        assert deleted == 1

    def test_delete_forwards_its_arguments_to_the_model(self):
        recorder = Recorder.objects.create(name="rec-1")
        with patch.object(models.Model, "delete", autospec=True, return_value=(1, {})) as spy:
            recorder.delete("default", keep_parents=True)
        assert spy.call_args.args[1:] == ("default",)
        assert spy.call_args.kwargs == {"keep_parents": True}

    def test_delete_forwards_its_keyword_arguments_to_the_hooks(self):
        recorder = Recorder.objects.create(name="rec-1")
        with patch.object(Recorder, "_run_hooked_methods", autospec=True) as spy:
            recorder.delete(keep_parents=True)
        assert [call.args[1] for call in spy.call_args_list] == [BEFORE_DELETE, AFTER_DELETE]
        assert all(call.kwargs == {"keep_parents": True} for call in spy.call_args_list)

    def test_update_persists_fields_a_hook_mutates_beyond_the_explicit_kwargs(self):
        # Recorder's BEFORE_SAVE hook sets self.slug from self.name - update(name=...) only
        # tells save() about "name", so slug must be widened into update_fields for the
        # hook's change to actually reach the database instead of being silently dropped.
        recorder = Recorder.objects.create(name="REC-1")
        recorder.update(name="REC-2")
        persisted = Recorder.objects.get(pk=recorder.pk)
        assert persisted.name == "REC-2"
        assert persisted.slug == "rec-2"

    def test_plain_save_without_update_fields_is_unaffected_by_the_widening_logic(self):
        recorder = Recorder.objects.create(name="REC-1")
        recorder.name = "REC-2"
        recorder.save()
        persisted = Recorder.objects.get(pk=recorder.pk)
        assert persisted.name == "REC-2"
        assert persisted.slug == "rec-2"


def triggers_of(model):
    return {trigger.name for trigger in getattr(model._meta, "triggers", [])}


class TestComposingThePieces:
    """BaseModel is four abstract models; a model can take any of them alone."""

    def test_base_model_is_the_four_together(self):
        assert BaseModel.__bases__ == (UUIDPrimaryKeyModel, DatabaseTimestampsModel, FullCleanOnSaveModel, ReprModel)

    @pytest.mark.django_db
    def test_timestamps_alone_keep_their_triggers_and_django_keeps_its_pk(self):
        note = TimestampedNote.objects.create(text="a")
        note.refresh_from_db()
        created, updated = note.created_at, note.updated_at

        TimestampedNote.objects.filter(pk=note.pk).update(text="b")
        note.refresh_from_db()

        assert isinstance(note.pk, int)
        assert note.created_at == created
        # The trigger stamps the update's own time, later than the insert's - so the trigger fired.
        assert note.updated_at > updated
        assert {"protect_created_at", "stamp_updated_at"} <= triggers_of(TimestampedNote)

    @pytest.mark.django_db
    def test_timestamps_alone_dont_validate_on_save(self):
        TimestampedNote(text="x" * 10).save()

    def test_a_model_without_the_timestamps_gets_no_triggers(self):
        assert not {"protect_created_at", "stamp_updated_at"} & triggers_of(CleanedNote)

    @pytest.mark.django_db
    def test_full_clean_on_save_alone_still_validates(self):
        with pytest.raises(ValidationError):
            CleanedNote(count=-1).save()

        assert CleanedNote.objects.count() == 0

    @pytest.mark.django_db
    def test_repr_alone_formats_over_any_pk(self):
        note = CleanedNote.objects.create(count=2)

        assert repr(note) == f"CleanedNote(id={note.pk})"
        assert str(note) == "note of 2"

    @pytest.mark.django_db
    def test_as_queryset_is_this_row_by_pk(self):
        widget = Widget.objects.create(name="bolt")
        Widget.objects.create(name="nut")

        assert list(widget.as_queryset()) == [widget]


@pytest.mark.parametrize("before", [True, False])
def test_skip_full_clean_puts_back_whatever_was_set_before(before):
    widget = Widget(name="bolt", count=1)
    widget.SKIP_FULL_CLEAN = before

    with widget.skip_full_clean():
        assert widget.SKIP_FULL_CLEAN is True

    assert widget.SKIP_FULL_CLEAN is before


def fresh_model(prefix, base, **meta):
    # Uniquely named: pgtrigger keeps a process-wide registry keyed by table, which a second run of the
    # same test in one process (as mutation testing does) would otherwise collide with.
    meta_class = type("Meta", (), {"app_label": "testapp", **meta})
    return type(f"{prefix}{uuid.uuid4().hex[:8]}", (base,), {"__module__": __name__, "Meta": meta_class})


class TestTriggersAttachAsEachModelIsDefined:
    """Models defined here, so the registration runs while the test does."""

    @isolate_apps("tests.testapp")
    def test_a_concrete_model_with_the_timestamps_gets_both(self):
        assert {"protect_created_at", "stamp_updated_at"} <= triggers_of(
            fresh_model("StampedNow", DatabaseTimestampsModel)
        )

    @isolate_apps("tests.testapp")
    def test_an_abstract_one_and_a_model_without_them_get_none(self):
        abstract = fresh_model("AbstractNow", DatabaseTimestampsModel, abstract=True)
        plain = fresh_model("PlainNow", models.Model)

        assert not {"protect_created_at", "stamp_updated_at"} & (triggers_of(abstract) | triggers_of(plain))


class TestVersionedModel:
    def test_its_trigger_moves_the_version_on_from_the_old_row(self):
        (bump_version,) = base_models._version_triggers(VersionedWidget)

        assert bump_version.name == "bump_version"
        assert bump_version.when == pgtrigger.Before
        assert bump_version.operation == pgtrigger.Update
        assert bump_version.get_func(VersionedWidget) == (
            'IF ((OLD."created_at" IS DISTINCT FROM (NEW."created_at") OR OLD."id" IS DISTINCT FROM (NEW."id") OR '
            'OLD."name" IS DISTINCT FROM (NEW."name"))) OR NEW."version" > OLD."version" OR '
            'NEW."updated_at" > OLD."updated_at" THEN NEW."version" = OLD."version" + 1; '
            'ELSE NEW."version" = OLD."version"; END IF; RETURN NEW;'
        )

    def test_its_trigger_follows_the_column_the_version_is_stored_in(self):
        (bump_version,) = base_models._version_triggers(VersionedNote)

        assert bump_version.get_func(VersionedNote) == (
            'IF ((OLD."id" IS DISTINCT FROM (NEW."id") OR OLD."text" IS DISTINCT FROM (NEW."text"))) OR '
            'NEW."Revision" > OLD."Revision" THEN NEW."Revision" = OLD."Revision" + 1; '
            'ELSE NEW."Revision" = OLD."Revision"; END IF; RETURN NEW;'
        )

    def test_a_new_row_is_version_one(self):
        assert VersionedNote.objects.create(text="a").version == 1

    def test_every_kind_of_update_moves_it_on_by_one(self):
        note = VersionedNote.objects.create(text="a")
        note.text = "b"
        note.save()
        VersionedNote.objects.filter(pk=note.pk).update(text="c")
        VersionedNote.objects.bulk_update([note], ["text"])

        note.refresh_from_db()
        assert note.version == 4

    def test_a_write_naming_a_version_of_its_own_still_moves_it_on_by_one(self):
        note = VersionedNote.objects.create(text="a")
        VersionedNote.objects.filter(pk=note.pk).update(version=99)
        note.refresh_from_db()
        assert note.version == 2

    def test_it_is_read_only_to_a_form_or_serializer(self):
        assert VersionedWidget._meta.get_field("version").editable is False

    def test_with_the_timestamps_a_model_gets_every_trigger(self):
        assert {"protect_created_at", "stamp_updated_at", "bump_version"} <= triggers_of(VersionedWidget)

    def test_alone_it_gets_only_its_own(self):
        assert triggers_of(VersionedNote) == {"bump_version"}

    def test_a_model_without_it_gets_no_version_trigger(self):
        assert "bump_version" not in triggers_of(Widget)

    @isolate_apps("tests.testapp")
    def test_an_abstract_one_gets_none(self):
        assert not triggers_of(fresh_model("AbstractVersioned", VersionedModel, abstract=True))


class TestAProxy:
    """A proxy shares its concrete model's table, whose triggers it doesn't register a second time."""

    def test_of_a_base_model_registers_no_triggers_of_its_own(self):
        assert triggers_of(WidgetProxy) == set()
        assert {"protect_created_at", "stamp_updated_at"} <= triggers_of(Widget)

    @isolate_apps("tests.testapp")
    def test_defined_now_is_left_alone(self):
        for base in (TimestampedNote, VersionedNote):
            proxy = fresh_model("ProxyNow", base, proxy=True)

            assert triggers_of(proxy) == set()

    @pytest.mark.django_db
    def test_writes_through_it_are_stamped_by_its_tables_triggers(self):
        widget = WidgetProxy.objects.create(name="bolt")
        widget.refresh_from_db()
        stamped = widget.updated_at
        WidgetProxy.objects.filter(pk=widget.pk).update(name="nut")
        widget.refresh_from_db()

        assert widget.updated_at > stamped


class TestBypassHooksFor:
    """django-lifecycle checks the bypass inside its own save(), which FullCleanOnSaveModel replaces
    rather than extends - so every one of these would pass vacuously if it asked nothing itself."""

    def test_it_skips_the_save_hooks(self):
        recorder = Recorder(name="rec-1")
        with bypass_hooks_for([Recorder]):
            recorder.save()
        assert recorder.hook_log == []

    def test_it_skips_the_delete_hooks_that_upstream_leaves_running(self):
        recorder = Recorder.objects.create(name="rec-1")
        recorder.hook_log = []
        with bypass_hooks_for([Recorder]):
            recorder.delete()
        assert recorder.hook_log == []

    def test_a_bypassed_delete_still_forwards_its_arguments_to_the_model(self):
        recorder = Recorder.objects.create(name="rec-1")
        with (
            bypass_hooks_for([Recorder]),
            patch.object(models.Model, "delete", autospec=True, return_value=(1, {})) as spy,
        ):
            recorder.delete("default", keep_parents=True)
        assert spy.call_args.args[1:] == ("default",)
        assert spy.call_args.kwargs == {"keep_parents": True}

    def test_a_model_it_does_not_name_still_runs_its_hooks(self):
        recorder = Recorder(name="rec-1")
        with bypass_hooks_for([Widget]):
            recorder.save()
        assert recorder.hook_log == ["before_create", "before_save", "after_save", "after_create"]

    def test_the_hooks_are_back_once_it_exits(self):
        recorder = Recorder(name="rec-1")
        with bypass_hooks_for([Recorder]):
            pass
        recorder.save()
        assert recorder.hook_log == ["before_create", "before_save", "after_save", "after_create"]

    def test_it_still_runs_full_clean(self):
        """Whether a row is valid is not a question about hooks, so bypassing them answers nothing
        about it - only SKIP_FULL_CLEAN does."""
        widget = Widget.objects.create(name="bolt", count=1)
        widget.count = -9
        with bypass_hooks_for([Widget]), pytest.raises(ValidationError):
            widget.save()

    def test_combined_with_skip_full_clean_it_bypasses_validation_too(self):
        widget = Widget.objects.create(name="bolt", count=1)
        with bypass_hooks_for([Widget]), widget.skip_full_clean():
            widget.update(count=-9)
        assert Widget.objects.get(pk=widget.pk).count == -9


class TestPrimaryKeys:
    @pytest.fixture
    def fresh(self, monkeypatch):
        """uuid7() as a new process has it: no key made before."""
        monkeypatch.setattr(base_models, "_last_milliseconds", 0)
        monkeypatch.setattr(base_models, "_last_counter", 0)

    def counter(self, made):
        """`made`'s counter - its first 12 bits in rand_a, its other 30 after the variant."""
        return (made.int >> 64 & 0xFFF) << 30 | made.int >> 32 & (1 << 30) - 1

    def at(self, monkeypatch, milliseconds):
        monkeypatch.setattr(base_models.time, "time_ns", lambda: milliseconds * 1_000_000 + 999_999)

    def test_a_version_7_uuid_is_laid_out_as_rfc_9562_has_it(self, fresh, monkeypatch):
        self.at(monkeypatch, 0x0123456789AB)
        monkeypatch.setattr(base_models.os, "urandom", lambda size: bytes(range(1, size + 1)))
        counter = int.from_bytes(bytes(range(1, 7))) >> 7

        made = base_models.uuid7()

        assert made.int >> 80 == 0x0123456789AB
        assert made.version == 7
        assert made.variant == uuid.RFC_4122
        assert made.int >> 64 & 0xFFF == counter >> 30
        assert made.int >> 32 & (1 << 30) - 1 == counter & (1 << 30) - 1
        assert made.int & 0xFFFFFFFF == int.from_bytes(bytes(range(1, 5)))

    def test_every_bit_is_used(self, fresh, monkeypatch):
        self.at(monkeypatch, 1)
        monkeypatch.setattr(base_models.os, "urandom", lambda size: b"\xff" * size)
        counter = (1 << 41) - 1

        assert base_models.uuid7().int == (
            1 << 80 | 7 << 76 | (counter >> 30) << 64 | 0b10 << 62 | (counter & (1 << 30) - 1) << 32 | 0xFFFFFFFF
        )

    def test_its_time_is_only_48_bits(self, fresh, monkeypatch):
        self.at(monkeypatch, (1 << 48) + 5)

        assert base_models.uuid7().int >> 80 == 5

    def test_within_a_millisecond_the_counter_counts_up(self, fresh, monkeypatch):
        self.at(monkeypatch, 1000)
        first, second = base_models.uuid7(), base_models.uuid7()

        assert (second.int >> 80, self.counter(second)) == (1000, self.counter(first) + 1)

    def test_a_clock_stepping_back_keeps_counting(self, fresh, monkeypatch):
        monkeypatch.setattr(base_models, "_last_milliseconds", 2000)
        monkeypatch.setattr(base_models, "_last_counter", 41)
        self.at(monkeypatch, 1000)

        made = base_models.uuid7()

        assert (made.int >> 80, self.counter(made)) == (2000, 42)

    def test_a_millisecond_counted_out_takes_the_next(self, fresh, monkeypatch):
        monkeypatch.setattr(base_models, "_last_milliseconds", 2000)
        monkeypatch.setattr(base_models, "_last_counter", (1 << 42) - 1)
        monkeypatch.setattr(base_models.os, "urandom", lambda size: b"\x00" * size)
        self.at(monkeypatch, 2000)

        made = base_models.uuid7()

        assert (made.int >> 80, self.counter(made)) == (2001, 0)
        assert (base_models._last_milliseconds, base_models._last_counter) == (2001, 0)

    def test_a_new_millisecond_starts_the_counter_afresh(self, fresh, monkeypatch):
        monkeypatch.setattr(base_models, "_last_milliseconds", 2000)
        monkeypatch.setattr(base_models, "_last_counter", 41)
        monkeypatch.setattr(base_models.os, "urandom", lambda size: b"\x00" * size)
        self.at(monkeypatch, 2001)

        made = base_models.uuid7()

        assert (made.int >> 80, self.counter(made)) == (2001, 0)

    def test_each_sorts_after_the_one_before(self):
        made = [base_models.uuid7() for _ in range(2000)]

        assert made == sorted(made)
        assert len(set(made)) == len(made)

    def test_later_ones_sort_later(self):
        first = base_models.uuid7()
        time.sleep(0.002)

        assert base_models.uuid7() > first

    def test_the_default_is_a_uuid7(self, settings):
        if hasattr(settings, "ISIK_PRIMARY_KEY"):
            del settings.ISIK_PRIMARY_KEY

        assert base_models.new_primary_key().version == 7

    def test_the_setting_picks_another(self, settings):
        settings.ISIK_PRIMARY_KEY = "uuid.uuid4"

        assert base_models.new_primary_key().version == 4

    @pytest.mark.django_db
    def test_is_what_a_new_row_gets(self):
        assert Widget.objects.create(name="bolt").pk.version == 7

    def test_the_field_names_the_function_not_a_kind_of_key(self):
        assert UUIDPrimaryKeyModel._meta.get_field("id").default is base_models.new_primary_key

    @pytest.mark.django_db
    def test_the_database_makes_one_too(self):
        before = time.time_ns() // 1_000_000
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {base_models.DatabaseUUID7().as_sql(None, connection)[0]}")
            [made] = cursor.fetchone()
        after = time.time_ns() // 1_000_000

        assert (made.version, made.variant) == (7, uuid.RFC_4122)
        assert before <= made.int >> 80 <= after

    @pytest.mark.django_db
    def test_the_databases_sort_in_the_order_theyre_made(self):
        made = base_models.DatabaseUUID7().as_sql(None, connection)[0]
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {made} FROM generate_series(1, 2000) ORDER BY generate_series")
            keys = [key for (key,) in cursor.fetchall()]

        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)

    @pytest.mark.django_db
    def test_the_databases_carry_the_fraction_of_their_millisecond(self):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            sql = (
                base_models.DatabaseUUID7()
                .as_sql(None, connection)[0]
                .replace("clock_timestamp()", "'2026-10-30 06:51:48.123750+00'::timestamptz")
            )
            cursor.execute(f"SELECT {sql}")
            [made] = cursor.fetchone()

        assert made.int >> 80 == 1793343108123
        # 750 microseconds into the millisecond, in 4096ths of it.
        assert made.int >> 64 & 0xFFF == 3072


def test_the_app_is_labelled_so_a_projects_common_can_be_common():
    from django.apps import apps

    assert apps.get_app_config("isik_common").name == "isik.django.apps.common"


class TestAWriteThatChangesNothing:
    """The kept fields move for a change to the row and only for one - and never back."""

    def read(self, widget):
        return VersionedWidget.objects.values_list("version", "updated_at").get(pk=widget.pk)

    def test_a_save_changing_nothing_moves_neither(self):
        widget = VersionedWidget.objects.create(name="bolt")
        before = self.read(widget)

        VersionedWidget.objects.get(pk=widget.pk).save()
        VersionedWidget.objects.filter(pk=widget.pk).update(name="bolt")

        assert self.read(widget) == before

    def test_a_change_moves_both(self):
        widget = VersionedWidget.objects.create(name="bolt")
        version, updated_at = self.read(widget)

        VersionedWidget.objects.filter(pk=widget.pk).update(name="nut")

        assert self.read(widget)[0] == version + 1
        assert self.read(widget)[1] > updated_at

    def test_a_stale_instance_saved_again_doesnt_take_them_back(self):
        widget = VersionedWidget.objects.create(name="bolt")
        widget.refresh_from_db()
        widget.name = "nut"
        widget.save()
        moved = self.read(widget)

        widget.save()

        assert (widget.version, self.read(widget)) == (1, moved)
        assert moved[0] == 2

    def test_an_older_version_written_alone_doesnt_take(self):
        widget = VersionedWidget.objects.create(name="bolt")
        VersionedWidget.objects.filter(pk=widget.pk).update(name="nut")
        moved = self.read(widget)

        VersionedWidget.objects.filter(pk=widget.pk).update(version=1)

        assert self.read(widget) == moved

    @pytest.mark.parametrize("field", ["version", "updated_at"])
    def test_either_moved_forward_alone_moves_both(self, field):
        widget = VersionedWidget.objects.create(name="bolt")
        version, updated_at = self.read(widget)
        step = 1 if field == "version" else datetime.timedelta(microseconds=1)

        VersionedWidget.objects.filter(pk=widget.pk).update(**{field: F(field) + step})

        assert self.read(widget)[0] == version + 1
        assert self.read(widget)[1] > updated_at


class TestTouchRows:
    def test_it_moves_the_kept_fields_of_every_row_it_names(self):
        for name in ("bolt", "nut"):
            VersionedWidget.objects.create(name=name)
        untouched = VersionedWidget.objects.create(name="washer")

        assert base_models.touch_rows(VersionedWidget.objects.exclude(pk=untouched.pk)) == 2

        assert sorted(VersionedWidget.objects.values_list("name", "version")) == [
            ("bolt", 2),
            ("nut", 2),
            ("washer", 1),
        ]

    def test_updated_at_alone_moves_too(self):
        note = TimestampedNote.objects.create(text="a")
        before = TimestampedNote.objects.values_list("updated_at", flat=True).get(pk=note.pk)

        base_models.touch_rows(TimestampedNote.objects.filter(pk=note.pk))

        assert TimestampedNote.objects.values_list("updated_at", flat=True).get(pk=note.pk) > before

    @pytest.mark.parametrize(
        ("model", "field", "step", "keeping"),
        [
            (VersionedWidget, "version", 1, "bump_version"),
            (TimestampedNote, "updated_at", datetime.timedelta(microseconds=1), "stamp_updated_at"),
        ],
    )
    def test_it_writes_one_step_forward(self, model, field, step, keeping):
        # The trigger keeping the field sets it itself - without it, what touch_rows() wrote stays.
        made = model.objects.create(**({"name": "bolt"} if model is VersionedWidget else {"text": "a"}))
        before = model.objects.values_list(field, flat=True).get(pk=made.pk)

        with pgtrigger.ignore(f"testapp.{model.__name__}:{keeping}"):
            base_models.touch_rows(model.objects.filter(pk=made.pk))

        assert model.objects.values_list(field, flat=True).get(pk=made.pk) == before + step

    def test_a_model_keeping_neither_is_refused(self):
        with pytest.raises(TypeError) as raised:
            base_models.touch_rows(CleanedNote.objects.all())

        assert str(raised.value) == "touch_rows() moves version and updated_at, and testapp.CleanedNote has neither."


def test_the_kept_fields_of_each_kind_of_model():
    assert base_models.database_kept_fields(VersionedWidget) == ("version", "updated_at")
    assert base_models.database_kept_fields(VersionedNote) == ("version",)
    assert base_models.database_kept_fields(TimestampedNote) == ("updated_at",)
    assert base_models.database_kept_fields(CleanedNote) == ()


@isolate_apps("tests.testapp")
def test_a_generated_column_isnt_compared():
    # A BEFORE trigger's NEW doesn't hold it yet - it follows the other columns anyway.
    made = type(
        f"Generated{uuid.uuid4().hex[:8]}",
        (DatabaseTimestampsModel,),
        {
            "__module__": __name__,
            "Meta": type("Meta", (), {"app_label": "testapp"}),
            "text": models.TextField(),
            "shout": models.GeneratedField(expression=Upper("text"), output_field=models.TextField(), db_persist=True),
        },
    )

    func = base_models._timestamp_triggers(made)[1].get_func(made)

    assert '"text"' in func
    assert '"shout"' not in func


class TestARelationMovingItsOwner:
    def test_the_first_kept_field_goes_forward(self):
        assert base_models._forward(VersionedWidget, '"id"') == '"version" = "version" + 1'
        assert base_models._forward(Widget, '"id"') == '"updated_at" = "updated_at" + interval \'1 microsecond\''

    def test_an_owner_keeping_none_is_updated_to_itself(self):
        assert base_models._forward(CleanedNote, '"id"') == '"id" = "id"'


def test_a_column_strategy_owner_keeping_neither_is_updated_by_its_key(monkeypatch):
    monkeypatch.setattr(CleanedNote, "concurrency_strategy", "column", raising=False)
    key = SimpleNamespace(target_field=SimpleNamespace(column="id"), column="note_id")

    trigger = base_models._relation_lock_trigger(CleanedNote, "notes", key)

    assert 'UPDATE "testapp_cleanednote" SET "id" = "id" WHERE "id" = NEW."note_id";' in trigger.func
