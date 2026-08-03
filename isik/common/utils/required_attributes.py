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

    Set `is_base_class = True` on an intermediate that's meant to stay abstract a while longer
    (e.g. a project's own `class BaseModelViewSet(_BaseModelViewSet): is_base_class = True`) to
    exempt just that one class without redeclaring `required_attributes` as a workaround:

        class BaseModelViewSet(ViewSet):
            is_base_class = True  # still no `endpoint` - fine, not checked on this class

        class WidgetViewSet(BaseModelViewSet):
            endpoint = "/widgets"  # checked normally, since it doesn't set is_base_class itself
    """

    required_attributes = []
    is_base_class = False

    def __init_subclass__(cls, **kwargs):
        # Checked before delegating to super() (unlike most cooperative __init_subclass__ hooks)
        # so this runs before any other mixin's own __init_subclass__ logic - e.g. a registry
        # mixin further down the MRO shouldn't register a class that's missing a required
        # attribute in the first place.
        #
        # A class is exempt from its own check if it redeclares `required_attributes` (it's the
        # one setting the REQUIRED sentinels, not the one meant to override them) or sets
        # `is_base_class = True`. Both are read from cls.__dict__ specifically, not via getattr,
        # so the exemption applies only to the class that sets it directly - not to its
        # descendants, which inherit the attribute but not the exemption.
        exempt = "required_attributes" in cls.__dict__ or cls.__dict__.get("is_base_class", False)
        if not exempt:
            for name in cls.required_attributes:
                if getattr(cls, name, REQUIRED) is REQUIRED:
                    raise TypeError(f"{cls.__name__} must define a `{name}` attribute")
        super().__init_subclass__(**kwargs)
