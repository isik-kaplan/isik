import pytest

from isik.django.apps.common.db.orm import get_object_or_none, starts_with
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


def test_starts_with_annotates_true_when_the_prefix_matches():
    Widget.objects.create(name="bolt-large", count=1)
    widget = Widget.objects.annotate(is_bolt=starts_with("name", "bolt")).get()
    assert widget.is_bolt is True


def test_starts_with_annotates_false_when_the_prefix_does_not_match():
    Widget.objects.create(name="nut-large", count=1)
    widget = Widget.objects.annotate(is_bolt=starts_with("name", "bolt")).get()
    assert widget.is_bolt is False


def test_get_object_or_none_returns_the_object_when_found():
    widget = Widget.objects.create(name="bolt", count=1)
    assert get_object_or_none(Widget, pk=widget.pk) == widget


def test_get_object_or_none_returns_none_when_not_found():
    assert get_object_or_none(Widget, name="does-not-exist") is None


def test_get_object_or_none_returns_none_for_an_invalid_lookup_value():
    # An invalid UUID string would normally raise ValidationError, not just DoesNotExist.
    assert get_object_or_none(Widget, pk="not-a-uuid") is None


class TestWithPythonDefaults:
    def test_a_moment_the_database_would_stamp_is_stamped_now(self):
        import datetime

        from django.utils import timezone

        from isik.django.apps.common.db import with_python_defaults
        from tests.testapp.models import Widget

        before = timezone.now()
        widget = with_python_defaults(Widget(name="bolt"))

        assert isinstance(widget.created_at, datetime.datetime)
        assert before <= widget.created_at <= timezone.now()
        assert before <= widget.updated_at <= timezone.now()

    def test_a_literal_is_its_value(self):
        from isik.django.apps.common.db import with_python_defaults
        from tests.testapp.models import VersionedNote

        assert with_python_defaults(VersionedNote(text="a")).version == 1

    def test_a_value_wrapped_in_value_is_its_value(self):
        from django.db import models
        from django.test.utils import isolate_apps

        from isik.django.apps.common.db import with_python_defaults

        @isolate_apps("tests.testapp")
        def define():
            class Wrapped(models.Model):
                name = models.CharField(max_length=10, db_default=models.Value("unnamed"))

                class Meta:
                    app_label = "testapp"

            return Wrapped

        assert with_python_defaults(define()()).name == "unnamed"

    def test_a_value_already_set_is_kept(self):
        from isik.django.apps.common.db import with_python_defaults
        from tests.testapp.models import VersionedNote

        assert with_python_defaults(VersionedNote(text="a", version=7)).version == 7

    def test_another_expression_is_refused_naming_the_field(self):
        from django.db import models
        from django.db.models.functions import Lower
        from django.test.utils import isolate_apps

        from isik.django.apps.common.db import with_python_defaults

        @isolate_apps("tests.testapp")
        def define():
            class Lowered(models.Model):
                name = models.CharField(max_length=10, db_default=Lower(models.Value("A")))

                class Meta:
                    app_label = "testapp"

            return Lowered

        with pytest.raises(ValueError) as raised:
            with_python_defaults(define()())

        assert str(raised.value).startswith(
            "testapp.Lowered.name's db_default Lower(Value('A')) has no value in Python"
        )
