import pytest

from isik.django.apps.common.orm import get_object_or_none, starts_with
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
