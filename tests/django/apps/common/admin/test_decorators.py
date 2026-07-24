import pytest

from isik.django.apps.common.admin import BaseAdmin, action
from tests.testapp.models import Widget


pytestmark = pytest.mark.django_db


def make_admin(**attrs):
    attrs.setdefault("search_fields", ["name"])
    admin_cls = type("TestAdmin", (BaseAdmin,), attrs)
    return admin_cls(Widget, None)


def test_action_sets_the_admin_short_description():
    @action("Do the thing")
    def do_thing(modeladmin, request, queryset):
        return queryset

    assert do_thing.short_description == "Do the thing"


def test_action_normalizes_a_single_instance_into_a_queryset(admin_site, rf):
    widget = Widget.objects.create(name="bolt", count=1)
    seen = {}

    @action("Do the thing")
    def do_thing(modeladmin, request, queryset):
        seen["queryset"] = queryset

    admin = make_admin()
    do_thing(admin, rf.get("/"), widget)

    assert list(seen["queryset"]) == [widget]


def test_action_still_accepts_a_real_queryset(admin_site, rf):
    Widget.objects.create(name="bolt", count=1)
    Widget.objects.create(name="nut", count=2)
    seen = {}

    @action("Do the thing")
    def do_thing(modeladmin, request, queryset):
        seen["count"] = queryset.count()

    admin = make_admin()
    do_thing(admin, rf.get("/"), Widget.objects.all())

    assert seen["count"] == 2


def test_action_rolls_back_db_writes_if_the_action_raises(admin_site, rf):
    widget = Widget.objects.create(name="bolt", count=1)

    class Boom(Exception):
        pass

    @action("Do the thing")
    def do_thing(modeladmin, request, queryset):
        Widget.objects.create(name="nut", count=2)
        raise Boom("boom")

    admin = make_admin()
    with pytest.raises(Boom):
        do_thing(admin, rf.get("/"), widget)

    assert not Widget.objects.filter(name="nut").exists()
