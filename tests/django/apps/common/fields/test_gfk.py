import pytest
from django.db import connection, models
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


def test_ct_field_name_and_fk_field_name_start_out_as_none_before_contribute_to_class():
    field = AutoGenericForeignKey()
    assert field.ct_field_name is None
    assert field.fk_field_name is None


def test_positional_args_still_reach_the_underlying_generic_foreign_key():
    # GenericForeignKey's own first two positional args are ct_field/fk_field.
    field = AutoGenericForeignKey("custom_ct", "custom_fk")
    assert field.ct_field == "custom_ct"
    assert field.fk_field == "custom_fk"


def test_keyword_args_also_still_reach_the_underlying_generic_foreign_key():
    # Distinct from the positional-args test above - this exercises **kwargs forwarding
    # specifically, not *args.
    field = AutoGenericForeignKey(ct_field="custom_ct", fk_field="custom_fk")
    assert field.ct_field == "custom_ct"
    assert field.fk_field == "custom_fk"


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


@isolate_apps("tests.testapp")
def test_contribute_to_class_wires_every_default_correctly():
    # A fresh isolate_apps model, not Note - Note is module-level and built once at import time,
    # so it would never observe a broken contribute_to_class() under a tool that reruns pytest in
    # the same process (e.g. mutation testing).
    class DefaultTarget(models.Model):
        target = AutoGenericForeignKey()

        class Meta:
            app_label = "testapp"

    ct_field = DefaultTarget._meta.get_field("target_content_type")
    id_field = DefaultTarget._meta.get_field("target_object_id")

    assert ct_field.remote_field.related_name == "defaulttarget_target+"
    assert ct_field.db_index is True
    assert ct_field.remote_field.on_delete is models.CASCADE
    assert ct_field.remote_field.limit_choices_to == {}
    assert id_field.get_internal_type() == "UUIDField"

    with connection.schema_editor() as editor:
        editor.create_model(DefaultTarget)
    widget = Widget.objects.create(name="bolt", count=1)
    instance = DefaultTarget.objects.create(target=widget)

    fetched = DefaultTarget.objects.get(pk=instance.pk)
    assert fetched.target == widget
    assert fetched.target_object_id == widget.id

    assert DefaultTarget._meta.get_field("target").name == "target"


@isolate_apps("tests.testapp")
def test_contribute_to_class_wires_a_real_limit_models_to_freshly():
    # A fresh model with limit_models_to actually set - the module-level Note (used by
    # test_content_type_field_enforces_the_limit_choices_to above) is built once at import time,
    # so it can't distinguish limit_choices_to=self.limit_gfk_models_to from a mutated None here.
    class RestrictedTarget(models.Model):
        target = AutoGenericForeignKey(limit_models_to=[Widget])

        class Meta:
            app_label = "testapp"

    content_type_field = RestrictedTarget._meta.get_field("target_content_type")
    assert content_type_field.remote_field.limit_choices_to == Q(app_label="testapp", model="widget")
