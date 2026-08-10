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

    The same class body redefining itself under the same name/module (e.g. a test's inline
    serializer re-executing because the test itself ran twice in one process, or a dev-server
    autoreload) just replaces the stale entry instead of raising - only a genuinely different
    class claiming an already-registered model is treated as a conflict.

    Set `is_base_class = True` on a project-level intermediate to give that branch its own private
    registry instead of sharing this one - several independent hierarchies can then each register
    the same model without colliding (see ViewSetRegistryMixin's docstring for the matching
    viewset-side example).
    """

    exempt_from_registry = False
    is_base_class = False
    model_map = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("is_base_class", False):
            # Must happen before the `model is None` guard below - a class marking itself as a
            # new base is almost always still abstract with no `Meta.model` of its own, which is
            # exactly the common case this fork needs to fire for.
            cls.model_map = {}
        if cls.exempt_from_registry:
            return
        model = getattr(getattr(cls, "Meta", None), "model", None)
        if model is None:
            return
        if model in cls.model_map:
            existing = cls.model_map[model]
            if (existing.__module__, existing.__qualname__) != (cls.__module__, cls.__qualname__):
                raise ImproperlyConfigured(f"{model} is already registered to {existing.__name__}")
        cls.model_map[model] = cls

    @classmethod
    def get_for_model(cls, model):
        return cls.model_map[model]

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
