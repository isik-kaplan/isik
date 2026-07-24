import pytest
from django.db import models
from django.db.models import Q
from django.test.utils import isolate_apps

from isik.django.apps.common.fields.gfk import AutoGenericForeignKey
from tests.testapp.models import Note, Tag, Widget


pytestmark = pytest.mark.django_db


def test_companion_fields_are_created_with_the_expected_names():
    field_names = {f.name for f in Note._meta.get_fields()}
    assert "target_content_type" in field_names
    assert "target_object_id" in field_names


def test_object_id_field_defaults_to_uuid_to_match_a_uuid_pk():
    field = Note._meta.get_field("target_object_id")
    assert field.get_internal_type() == "UUIDField"


def test_setting_the_target_persists_and_resolves_correctly():
    widget = Widget.objects.create(name="bolt", count=1)
    note = Note.objects.create(body="a note", target=widget)

    fetched = Note.objects.get(pk=note.pk)
    assert fetched.target == widget
    assert fetched.target_object_id == widget.id


def test_limit_gfk_models_to_restricts_to_the_given_models():
    field = AutoGenericForeignKey(limit_models_to=[Widget])
    assert field.limit_gfk_models_to == Q(app_label="testapp", model="widget")


def test_limit_gfk_models_to_accepts_dotted_app_label_strings():
    field = AutoGenericForeignKey(limit_models_to=["testapp.Tag"])
    assert field.limit_gfk_models_to == Q(app_label="testapp", model="tag")


def test_limit_gfk_models_to_combines_multiple_models_with_or():
    field = AutoGenericForeignKey(limit_models_to=[Widget, Tag])
    assert field.limit_gfk_models_to == (Q(app_label="testapp", model="widget") | Q(app_label="testapp", model="tag"))


def test_limit_gfk_models_to_is_none_when_unrestricted():
    field = AutoGenericForeignKey()
    assert field.limit_gfk_models_to is None


def test_content_type_field_enforces_the_limit_choices_to():
    content_type_field = Note._meta.get_field("target_content_type")
    assert content_type_field.remote_field.limit_choices_to == Q(app_label="testapp", model="widget")


def test_content_type_field_defaults_to_cascade_on_delete():
    content_type_field = Note._meta.get_field("target_content_type")
    assert content_type_field.remote_field.on_delete is models.CASCADE


@isolate_apps("tests.testapp")
def test_object_id_field_can_be_overridden_to_a_non_uuid_field():
    class CharTarget(models.Model):
        target = AutoGenericForeignKey(object_id_field=models.CharField, object_id_field_kwargs={"max_length": 255})

        class Meta:
            app_label = "testapp"

    field = CharTarget._meta.get_field("target_object_id")
    assert field.get_internal_type() == "CharField"
    assert field.max_length == 255


@isolate_apps("tests.testapp")
def test_on_delete_can_be_overridden_away_from_the_cascade_default():
    class ProtectedTarget(models.Model):
        target = AutoGenericForeignKey(on_delete=models.PROTECT)

        class Meta:
            app_label = "testapp"

    content_type_field = ProtectedTarget._meta.get_field("target_content_type")
    assert content_type_field.remote_field.on_delete is models.PROTECT
