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

    Set `is_base_class = True` on a project-level intermediate (e.g. a per-API
    `class BaseModelViewSet(_BaseModelViewSet): is_base_class = True`) to give that branch its own
    private registry instead of sharing this one - several independent hierarchies can then each
    register the same model without colliding.

        class AuthBaseModelViewSet(BaseModelViewSet):
            is_base_class = True

        class AuthWidgetViewSet(AuthBaseModelViewSet):
            model = Widget

        AuthBaseModelViewSet.get_for_model(Widget) is AuthWidgetViewSet
        ViewSetRegistryMixin.get_for_model(Widget)  # None - a different registry entirely
    """

    model = None
    exempt_from_registry = False
    is_base_class = False
    model_map = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("is_base_class", False):
            # Must happen before the `not cls.model` guard below - a class marking itself as a
            # new base is almost always still abstract with no `model` of its own, which is
            # exactly the common case this fork needs to fire for.
            cls.model_map = {}
        if not cls.model or cls.exempt_from_registry:
            return
        if cls.model in cls.model_map:
            existing = cls.model_map[cls.model]
            raise ImproperlyConfigured(f"{cls.model} is already registered to {existing.__name__}")
        cls.model_map[cls.model] = cls

    @classmethod
    def get_for_model(cls, model):
        return cls.model_map.get(model)
