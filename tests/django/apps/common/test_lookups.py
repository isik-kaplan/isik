import pytest

from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


def test_length_lookup_is_registered_on_char_fields():
    Widget.objects.create(name="bolt", count=1)
    Widget.objects.create(name="washer", count=1)
    assert list(Widget.objects.filter(name__length=4).values_list("name", flat=True)) == ["bolt"]


def test_length_lookup_can_be_used_for_comparisons():
    Widget.objects.create(name="bolt", count=1)
    Widget.objects.create(name="washer", count=1)
    assert Widget.objects.filter(name__length__gt=4).count() == 1
