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

    def test_is_base_class_forks_a_private_registry_for_that_branch(self):
        class DummyForkedModel:
            pass

        class AuthBase(ViewSetRegistryMixin, ModelViewSet):
            is_base_class = True

        class AuthWidgetViewSet(AuthBase):
            model = DummyForkedModel

        assert AuthBase.get_for_model(DummyForkedModel) is AuthWidgetViewSet
        # The forked registry is a different dict entirely, isolated from the global one.
        assert ViewSetRegistryMixin.get_for_model(DummyForkedModel) is None
        assert AuthBase.model_map is not ViewSetRegistryMixin.model_map

    def test_two_independent_is_base_class_hierarchies_dont_collide_on_the_same_model(self):
        class DummySharedModel:
            pass

        class PublicBase(ViewSetRegistryMixin, ModelViewSet):
            is_base_class = True

        class TenantBase(ViewSetRegistryMixin, ModelViewSet):
            is_base_class = True

        class PublicWidgetViewSet(PublicBase):
            model = DummySharedModel

        class TenantWidgetViewSet(TenantBase):
            model = DummySharedModel

        assert PublicBase.get_for_model(DummySharedModel) is PublicWidgetViewSet
        assert TenantBase.get_for_model(DummySharedModel) is TenantWidgetViewSet
