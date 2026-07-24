from isik.common.utils.sentinel import Sentinel


REQUIRED = Sentinel("REQUIRED")


class RequiredAttributesMixin:
    """
    Fails fast at class-definition time if a subclass forgets to override one of
    `required_attributes`, instead of surfacing as an AttributeError (or worse, silently wrong
    behavior) the first time something is actually used.

        class ViewSet(RequiredAttributesMixin):
            required_attributes = ["endpoint"]
            endpoint = REQUIRED

        class WidgetViewSet(ViewSet):
            pass  # raises TypeError: WidgetViewSet must define an `endpoint` attribute
    """

    required_attributes = []

    def __init_subclass__(cls, **kwargs):
        # Checked before delegating to super() (unlike most cooperative __init_subclass__ hooks)
        # so this runs before any other mixin's own __init_subclass__ logic - e.g. a registry
        # mixin further down the MRO shouldn't register a class that's missing a required
        # attribute in the first place.
        #
        # The class that declares `required_attributes` is itself exempt - it's the one setting
        # the REQUIRED sentinels in the first place, not the one meant to override them.
        if "required_attributes" not in cls.__dict__:
            for name in cls.required_attributes:
                if getattr(cls, name, REQUIRED) is REQUIRED:
                    raise TypeError(f"{cls.__name__} must define a `{name}` attribute")
        super().__init_subclass__(**kwargs)
