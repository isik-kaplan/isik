import pytest
from django.core.exceptions import ValidationError

from tests.testapp.models import Recorder, Widget


pytestmark = pytest.mark.django_db


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
