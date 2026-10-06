import uuid
from unittest.mock import patch

import pgtrigger
import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.test.utils import isolate_apps

from isik.django.apps.common.db import (
    BaseModel,
    DatabaseTimestampsModel,
    FullCleanOnSaveModel,
    ReprModel,
    UUIDPrimaryKeyModel,
)
from isik.django.apps.common.db import models as base_models
from tests.testapp.models import CleanedNote, ModifiedColumnNote, Recorder, TimestampedNote, Widget


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
    assert stamp_updated_at.func == 'NEW."updated_at" = NOW(); RETURN NEW;'


def test_timestamp_triggers_stamp_the_column_updated_at_is_stored_in():
    _, stamp_updated_at = base_models._timestamp_triggers(ModifiedColumnNote)
    assert stamp_updated_at.func == 'NEW."Modified" = NOW(); RETURN NEW;'


@pytest.mark.django_db
def test_an_update_stamps_updated_at_under_its_own_column_name():
    note = ModifiedColumnNote.objects.create(text="a")
    note.refresh_from_db()
    updated = note.updated_at
    ModifiedColumnNote.objects.filter(pk=note.pk).update(text="b")
    note.refresh_from_db()
    assert note.text == "b"
    assert note.updated_at != updated


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
        # The trigger stamps NOW() - the transaction's start, which inside a test's transaction can be
        # earlier than the insert's statement time. That it moved is what says the trigger fired.
        assert note.updated_at != updated
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
