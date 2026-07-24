class ActionSerializerClassMixin:
    """
    Picks a serializer class per action from `serializer_class_action_map`, instead of overriding
    get_serializer_class() by hand on every viewset that needs a different shape for one action.

        class WidgetViewSet(ActionSerializerClassMixin, ModelViewSet):
            serializer_class = WidgetSerializer
            serializer_class_action_map = {"list": WidgetListSerializer}
    """

    serializer_class_action_map = {}

    def get_serializer_class(self):
        return self.serializer_class_action_map.get(self.action, self.serializer_class)
