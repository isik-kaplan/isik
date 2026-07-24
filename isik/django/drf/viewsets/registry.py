from django.core.exceptions import ImproperlyConfigured


class ViewSetRegistryMixin:
    """
    Maintains a model -> viewset-class registry, populated automatically as subclasses are
    defined - so you can look up "what's the API endpoint for this model" later (e.g. to build a
    cross-link, or a notification pointing at some other resource).

        ViewSetRegistryMixin.get_for_model(Widget) is WidgetViewSet

    Registering two viewsets for the same model is almost always a mistake, so it fails fast
    instead of silently letting the second one win. A subclass with no `model` set yet (e.g. an
    abstract intermediate still relying on RequiredAttributesMixin to enforce it later) is skipped
    rather than registered under a placeholder - as is one with `exempt_from_registry = True`.
    """

    model = None
    exempt_from_registry = False
    model_map = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.model or cls.exempt_from_registry:
            return
        if cls.model in ViewSetRegistryMixin.model_map:
            existing = ViewSetRegistryMixin.model_map[cls.model]
            raise ImproperlyConfigured(f"{cls.model} is already registered to {existing.__name__}")
        ViewSetRegistryMixin.model_map[cls.model] = cls

    @classmethod
    def get_for_model(cls, model):
        return ViewSetRegistryMixin.model_map.get(model)
