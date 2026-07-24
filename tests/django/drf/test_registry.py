import pytest
from django.core.exceptions import ImproperlyConfigured

from isik.django.drf.serializers.registry import ModelSerializerRegistryMixin


# Dummy stand-ins for "a model" - ModelSerializerRegistryMixin only ever reads cls.Meta.model as a
# dict key, it doesn't need a real Django model. Local classes here keep this file's registrations
# isolated from ModelSerializerRegistryMixin.model_map, which is shared process-wide.
class DummyWidgetModel:
    pass


class DummyTagModel:
    pass


class DummyUnregisteredModel:
    pass


class WidgetSerializer(ModelSerializerRegistryMixin):
    class Meta:
        model = DummyWidgetModel
        fields = ["id", "name"]


class ExemptTagSerializer(ModelSerializerRegistryMixin):
    exempt_from_registry = True

    class Meta:
        model = DummyTagModel
        fields = ["id", "label"]


class TestModelSerializerRegistryMixin:
    def test_get_for_model_returns_the_registered_serializer(self):
        assert ModelSerializerRegistryMixin.get_for_model(DummyWidgetModel) is WidgetSerializer

    def test_exempt_from_registry_keeps_a_serializer_out_of_the_map(self):
        with pytest.raises(KeyError):
            ModelSerializerRegistryMixin.get_for_model(DummyTagModel)

    def test_get_for_model_raises_key_error_for_an_unregistered_model(self):
        with pytest.raises(KeyError):
            ModelSerializerRegistryMixin.get_for_model(DummyUnregisteredModel)

    def test_registering_a_second_serializer_for_the_same_model_raises(self):
        with pytest.raises(ImproperlyConfigured):

            class AnotherWidgetSerializer(ModelSerializerRegistryMixin):
                class Meta:
                    model = DummyWidgetModel
                    fields = ["id"]

    def test_a_serializer_with_a_meta_but_no_model_is_simply_skipped(self):
        class NoModelSerializer(ModelSerializerRegistryMixin):
            class Meta:
                fields = ["id"]

        assert NoModelSerializer not in ModelSerializerRegistryMixin.model_map.values()

    def test_a_serializer_with_no_meta_at_all_is_simply_skipped(self):
        class NoMetaSerializer(ModelSerializerRegistryMixin):
            pass

        assert NoMetaSerializer not in ModelSerializerRegistryMixin.model_map.values()


class TestModelSerializerMap:
    def test_maps_several_models_at_once(self):
        result = WidgetSerializer.model_serializer_map(DummyWidgetModel)
        assert result == {DummyWidgetModel: WidgetSerializer}

    def test_missing_model_raises_by_default(self):
        with pytest.raises(KeyError):
            WidgetSerializer.model_serializer_map(DummyWidgetModel, DummyUnregisteredModel)

    def test_ignore_missing_skips_unregistered_models(self):
        result = WidgetSerializer.model_serializer_map(DummyWidgetModel, DummyUnregisteredModel, ignore_missing=True)
        assert result == {DummyWidgetModel: WidgetSerializer}
