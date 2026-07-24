import pytest
from dalf.admin import DALFRelatedFieldAjax
from django.test import RequestFactory

from isik.django.apps.common.admin import BaseAdmin
from isik.django.apps.common.db import BaseModel
from tests.testapp.models import TaggedWidget, Widget


pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


def make_admin(model=Widget, admin_site=None, **attrs):
    attrs.setdefault("search_fields", ["name"])
    admin_cls = type("TestAdmin", (BaseAdmin,), attrs)
    return admin_cls(model, admin_site)


class TestGetListFilter:
    def test_combines_autocomplete_and_plain_filters(self, admin_site):
        admin = make_admin(list_filter=["count"], autocomplete_list_filter=["owner"])
        result = admin.get_list_filter(request=None)
        assert result == [("owner", DALFRelatedFieldAjax), "count"]

    def test_defaults_to_empty(self, admin_site):
        admin = make_admin()
        assert admin.get_list_filter(request=None) == []


def test_get_search_fields_returns_search_fields_as_is(admin_site):
    admin = make_admin(search_fields=["name", "count"])
    assert admin.get_search_fields(request=None) == ["name", "count"]


class TestGetReadonlyFields:
    def test_create_uses_create_and_global_readonly_fields(self, admin_site):
        admin = make_admin(create_readonly_fields=["name"], update_readonly_fields=["count"])
        assert admin.get_readonly_fields(request=None, obj=None) == ["name", *BaseModel.FIELDS]

    def test_update_uses_update_and_global_readonly_fields(self, admin_site):
        widget = Widget.objects.create(name="bolt", count=1)
        admin = make_admin(create_readonly_fields=["name"], update_readonly_fields=["count"])
        assert admin.get_readonly_fields(request=None, obj=widget) == ["count", *BaseModel.FIELDS]

    def test_global_readonly_fields_apply_in_both_cases(self, admin_site):
        widget = Widget.objects.create(name="bolt", count=1)
        admin = make_admin(global_readonly_fields=["name"])
        assert admin.get_readonly_fields(request=None, obj=None) == ["name", *BaseModel.FIELDS]
        assert admin.get_readonly_fields(request=None, obj=widget) == ["name", *BaseModel.FIELDS]


class TestGetListDisplay:
    def test_appends_base_model_fields_except_excluded(self, admin_site):
        admin = make_admin(list_display=["name"])
        assert admin.get_list_display(request=None) == ["name", *BaseModel.FIELDS]

    def test_excluded_list_display_removes_fields(self, admin_site):
        admin = make_admin(list_display=["name"], excluded_list_display=["id"])
        expected = ["name"] + [f for f in BaseModel.FIELDS if f != "id"]
        assert admin.get_list_display(request=None) == expected


def test_get_fieldsets_builds_object_and_meta_sections(admin_site):
    admin = make_admin(object_fieldsets=[[["name", "count"], "Widget"]])
    fieldsets = admin.get_fieldsets(request=None, obj=None)
    assert fieldsets[0] == ["Widget", {"fields": ("name", "count")}]
    assert fieldsets[-1][0] == "Meta"
    assert fieldsets[-1][1]["fields"] == tuple(BaseModel.FIELDS)


class TestSaveModel:
    def test_create_forces_create_and_global_fields_to_current_user(self, admin_site, rf, admin_user):
        admin = make_admin(create_force_field_as_current_user=["owner"])
        widget = Widget(name="bolt", count=1)
        request = rf.post("/")
        request.user = admin_user
        admin.save_model(request, widget, form=None, change=False)
        assert widget.owner == admin_user

    def test_update_does_not_use_create_force_fields(self, admin_site, rf, admin_user):
        widget = Widget.objects.create(name="bolt", count=1)
        admin = make_admin(create_force_field_as_current_user=["owner"])
        request = rf.post("/")
        request.user = admin_user
        admin.save_model(request, widget, form=None, change=True)
        assert widget.owner is None

    def test_global_force_field_applies_on_update_too(self, admin_site, rf, admin_user):
        widget = Widget.objects.create(name="bolt", count=1)
        admin = make_admin(global_force_field_as_current_user=["owner"])
        request = rf.post("/")
        request.user = admin_user
        admin.save_model(request, widget, form=None, change=True)
        assert widget.owner == admin_user


class TestFormfieldForManyToMany:
    def test_returns_none_for_non_auto_created_through_when_not_marked_safe(self, admin_site, rf):
        # Django's own formfield_for_manytomany hides these fields rather than raising.
        admin = make_admin(model=TaggedWidget, admin_site=admin_site)
        field = TaggedWidget._meta.get_field("tags")
        request = rf.get("/")
        assert admin.formfield_for_manytomany(field, request) is None

    def test_succeeds_when_field_is_marked_safe(self, admin_site, rf):
        admin = make_admin(model=TaggedWidget, admin_site=admin_site, safe_m2m_fields=["tags"])
        field = TaggedWidget._meta.get_field("tags")
        request = rf.get("/")

        form_field = admin.formfield_for_manytomany(field, request)

        assert form_field is not None

    def test_through_model_auto_created_flag_is_restored_afterward(self, admin_site, rf):
        admin = make_admin(model=TaggedWidget, admin_site=admin_site, safe_m2m_fields=["tags"])
        field = TaggedWidget._meta.get_field("tags")
        request = rf.get("/")

        admin.formfield_for_manytomany(field, request)

        assert field.remote_field.through._meta.auto_created is False
