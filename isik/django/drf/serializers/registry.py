from django.core.exceptions import ImproperlyConfigured


class ModelSerializerRegistryMixin:
    """
    Maintains a model -> serializer-class registry, populated automatically as subclasses are
    defined - so any serializer using this mixin can be looked up later by the model it serializes.

        class WidgetSerializer(ModelSerializerRegistryMixin, ModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name"]

        ModelSerializerRegistryMixin.get_for_model(Widget) is WidgetSerializer

    Registering two serializers for the same model is almost always a mistake, so it fails fast
    instead of silently letting the second one win (matches ViewSetRegistryMixin). Set
    `exempt_from_registry = True` on a subclass that shouldn't be registered at all - e.g. a
    schema-only serializer, or an intentional second serializer for a model already registered.
    """

    exempt_from_registry = False
    model_map = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.exempt_from_registry:
            return
        model = getattr(getattr(cls, "Meta", None), "model", None)
        if model is None:
            return
        if model in ModelSerializerRegistryMixin.model_map:
            existing = ModelSerializerRegistryMixin.model_map[model]
            raise ImproperlyConfigured(f"{model} is already registered to {existing.__name__}")
        ModelSerializerRegistryMixin.model_map[model] = cls

    @classmethod
    def get_for_model(cls, model):
        return ModelSerializerRegistryMixin.model_map[model]

    @classmethod
    def model_serializer_map(cls, *models, ignore_missing=False):
        """Same as get_for_model, for several models at once - {model: serializer_class}."""
        result = {}
        for model in models:
            try:
                result[model] = cls.get_for_model(model)
            except KeyError:
                if not ignore_missing:
                    raise
        return result
