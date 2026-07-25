import datetime
import decimal
import ipaddress
import uuid

import pytest
from rest_framework import serializers

from isik.django.drf.schema import FakeErrorSerializer, FakeSerializer
from tests.testapp.models import Widget


@pytest.fixture(autouse=True)
def _reset_used_names():
    FakeSerializer._used_names = set()


class TestFakeSerializer:
    def test_a_plain_type_maps_to_its_default_field(self):
        Fake = FakeSerializer("Fake", {"name": str, "count": int})
        assert isinstance(Fake().fields["name"], serializers.CharField)
        assert isinstance(Fake().fields["count"], serializers.IntegerField)

    def test_every_plain_python_type_in_the_default_map_resolves(self):
        Fake = FakeSerializer(
            "Fake",
            {
                "a": str,
                "b": int,
                "c": float,
                "d": bool,
                "e": list,
                "f": dict,
                "g": uuid.UUID,
                "h": datetime.date,
                "i": datetime.datetime,
                "j": datetime.time,
                "k": datetime.timedelta,
            },
        )
        fields = Fake().fields
        assert isinstance(fields["a"], serializers.CharField)
        assert isinstance(fields["b"], serializers.IntegerField)
        assert isinstance(fields["c"], serializers.FloatField)
        assert isinstance(fields["d"], serializers.BooleanField)
        assert isinstance(fields["e"], serializers.ListField)
        assert isinstance(fields["f"], serializers.DictField)
        assert isinstance(fields["g"], serializers.UUIDField)
        assert isinstance(fields["h"], serializers.DateField)
        assert isinstance(fields["i"], serializers.DateTimeField)
        assert isinstance(fields["j"], serializers.TimeField)
        assert isinstance(fields["k"], serializers.DurationField)

    def test_a_field_subclass_is_instantiated_with_no_arguments(self):
        Fake = FakeSerializer("Fake", {"id": serializers.UUIDField})
        assert isinstance(Fake().fields["id"], serializers.UUIDField)

    def test_an_already_built_field_instance_is_used_as_is_untouched_by_read_only_required(self):
        field = serializers.CharField(max_length=5)
        Fake = FakeSerializer("Fake", {"name": field}, read_only=True, required=True)
        assert Fake._declared_fields["name"] is field
        assert field.max_length == 5

    def test_an_already_built_field_instance_also_ignores_field_kwargs(self):
        field = serializers.CharField()
        Fake = FakeSerializer("Fake", {"name": field}, field_kwargs={"name": {"max_length": 5}})
        assert Fake._declared_fields["name"] is field
        assert field.max_length is None

    def test_omitting_read_only_and_required_leaves_the_fields_own_defaults_untouched(self):
        Fake = FakeSerializer("Fake", {"name": str})
        field = Fake().fields["name"]
        plain_field = serializers.CharField()
        assert field.read_only == plain_field.read_only
        assert field.required == plain_field.required

    def test_read_only_and_required_apply_to_type_and_class_derived_fields(self):
        Fake = FakeSerializer("Fake", {"name": str}, read_only=True, required=False)
        field = Fake().fields["name"]
        assert field.read_only is True
        assert field.required is False

    def test_field_kwargs_overrides_read_only_required_per_field(self):
        Fake = FakeSerializer(
            "Fake",
            {"name": str, "code": int},
            read_only=True,
            field_kwargs={"code": {"required": True, "read_only": False}},
        )
        fields = Fake().fields
        assert fields["name"].read_only is True
        assert fields["code"].read_only is False
        assert fields["code"].required is True

    def test_an_unrecognized_schema_value_raises_a_clear_error(self):
        with pytest.raises(TypeError, match="isn't a registered type"):
            FakeSerializer("Fake", {"price": decimal.Decimal})

    def test_register_types_extends_the_default_map(self):
        FakeSerializer.register_types({ipaddress.IPv4Address: serializers.IPAddressField})
        try:
            Fake = FakeSerializer("Fake", {"ip": ipaddress.IPv4Address})
            assert isinstance(Fake().fields["ip"], serializers.IPAddressField)
        finally:
            del FakeSerializer.type_fields[ipaddress.IPv4Address]

    def test_returns_a_class_not_an_instance(self):
        Fake = FakeSerializer("Fake", {"name": str})
        assert isinstance(Fake, type)
        assert Fake.__name__ == "Fake"

    def test_many_true_produces_a_list_serializer(self):
        Fake = FakeSerializer("Fake", {"name": str})
        instance = Fake(many=True)
        assert isinstance(instance, serializers.ListSerializer)

    def test_meta_fields_lists_every_schema_key(self):
        Fake = FakeSerializer("Fake", {"name": str, "count": int})
        assert Fake.Meta.fields == ["name", "count"]

    def test_base_lets_a_real_serializer_be_extended(self):
        class WidgetSerializer(serializers.Serializer):
            name = serializers.CharField()

        Fake = FakeSerializer("FakeWidget", {"extra": str}, base=WidgetSerializer)
        fields = Fake().fields
        assert "name" in fields
        assert "extra" in fields

    def test_reusing_a_name_without_reuse_true_raises(self):
        FakeSerializer("Fake", {"name": str})
        with pytest.raises(ValueError, match="reuse=True"):
            FakeSerializer("Fake", {"name": str})

    def test_reusing_a_name_with_reuse_true_is_allowed(self):
        FakeSerializer("Fake", {"name": str})
        Fake = FakeSerializer("Fake", {"name": str}, reuse=True)
        assert Fake.__name__ == "Fake"

    def test_a_different_name_never_needs_reuse(self):
        FakeSerializer("Fake", {"name": str})
        FakeSerializer("OtherFake", {"name": str})


class TestFakeErrorSerializer:
    class WidgetSerializer(serializers.Serializer):
        """A plain Serializer with no Meta at all - one of the two previously-unsupported shapes."""

        name = serializers.CharField()
        count = serializers.IntegerField()

    def test_builds_a_list_of_strings_field_per_source_field_plus_non_field_errors(self):
        ErrorSerializer = FakeErrorSerializer(self.WidgetSerializer)
        fields = ErrorSerializer().fields
        assert set(fields) == {"name", "count", "non_field_errors"}
        assert all(isinstance(f, serializers.ListField) for f in fields.values())

    def test_name_strips_the_serializer_suffix_and_adds_error(self):
        ErrorSerializer = FakeErrorSerializer(self.WidgetSerializer)
        assert ErrorSerializer.__name__ == "WidgetError"

    def test_extra_fields_are_merged_in(self):
        ErrorSerializer = FakeErrorSerializer(self.WidgetSerializer, extra_fields={"code": int})
        assert "code" in ErrorSerializer().fields

    def test_works_with_a_model_serializer_using_meta_exclude(self):
        class ExcludeSerializer(serializers.ModelSerializer):
            class Meta:
                model = Widget
                exclude = ["owner"]

        ErrorSerializer = FakeErrorSerializer(ExcludeSerializer)
        assert set(ErrorSerializer().fields) == {
            "id",
            "created_at",
            "updated_at",
            "name",
            "count",
            "non_field_errors",
        }

    def test_works_with_meta_fields_dunder_all(self):
        class AllFieldsSerializer(serializers.ModelSerializer):
            class Meta:
                model = Widget
                fields = "__all__"

        ErrorSerializer = FakeErrorSerializer(AllFieldsSerializer)
        assert "owner" in ErrorSerializer().fields

    def test_reusing_the_same_source_serializer_without_reuse_true_raises(self):
        FakeErrorSerializer(self.WidgetSerializer)
        with pytest.raises(ValueError, match="reuse=True"):
            FakeErrorSerializer(self.WidgetSerializer)

    def test_reusing_the_same_source_serializer_with_reuse_true_is_allowed(self):
        FakeErrorSerializer(self.WidgetSerializer)
        ErrorSerializer = FakeErrorSerializer(self.WidgetSerializer, reuse=True)
        assert ErrorSerializer.__name__ == "WidgetError"

    def test_different_extra_fields_under_the_same_derived_name_still_requires_reuse_true(self):
        FakeErrorSerializer(self.WidgetSerializer)
        with pytest.raises(ValueError, match="reuse=True"):
            FakeErrorSerializer(self.WidgetSerializer, extra_fields={"code": int})
