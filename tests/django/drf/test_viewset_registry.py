import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.viewsets import ModelViewSet

from isik.django.drf.viewsets.registry import ViewSetRegistryMixin


# Dummy stand-ins for "a model" - ViewSetRegistryMixin only ever reads cls.model as a dict key
# (ModelViewSet itself doesn't validate it at class-definition time either), so a real Django
# model isn't needed. Local classes here keep this file's registrations isolated from
# ViewSetRegistryMixin.model_map, which is shared process-wide.
class DummyWidgetModel:
    pass


class DummyTagModel:
    pass


class DummyUnregisteredModel:
    pass


class WidgetViewSet(ViewSetRegistryMixin, ModelViewSet):
    model = DummyWidgetModel


class TestViewSetRegistryMixin:
    def test_get_for_model_returns_the_registered_viewset(self):
        assert ViewSetRegistryMixin.get_for_model(DummyWidgetModel) is WidgetViewSet

    def test_get_for_model_returns_none_for_an_unregistered_model(self):
        assert ViewSetRegistryMixin.get_for_model(DummyUnregisteredModel) is None

    def test_registering_a_second_viewset_for_the_same_model_raises(self):
        with pytest.raises(ImproperlyConfigured):

            class AnotherWidgetViewSet(ViewSetRegistryMixin, ModelViewSet):
                model = DummyWidgetModel

    def test_exempt_from_registry_keeps_a_viewset_out_of_the_map(self):
        class ExemptTagViewSet(ViewSetRegistryMixin, ModelViewSet):
            model = DummyTagModel
            exempt_from_registry = True

        assert ViewSetRegistryMixin.get_for_model(DummyTagModel) is None

    def test_a_viewset_with_no_model_set_yet_is_not_registered(self):
        class AbstractIntermediate(ViewSetRegistryMixin, ModelViewSet):
            pass

        assert AbstractIntermediate not in ViewSetRegistryMixin.model_map.values()
