from rest_framework.viewsets import ModelViewSet

from isik.django.drf.viewsets.action_serializer_class import ActionSerializerClassMixin


class DefaultSerializer:
    pass


class ListSerializer:
    pass


class WidgetViewSet(ActionSerializerClassMixin, ModelViewSet):
    serializer_class = DefaultSerializer
    serializer_class_action_map = {"list": ListSerializer}


class TestActionSerializerClassMixin:
    def test_falls_back_to_serializer_class_for_an_unmapped_action(self):
        view = WidgetViewSet()
        view.action = "retrieve"
        assert view.get_serializer_class() is DefaultSerializer

    def test_uses_the_mapped_serializer_for_the_current_action(self):
        view = WidgetViewSet()
        view.action = "list"
        assert view.get_serializer_class() is ListSerializer
