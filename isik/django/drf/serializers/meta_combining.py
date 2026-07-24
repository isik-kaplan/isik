class MetaCombiningMixin:
    """
    Merges specific `Meta` attributes with the values declared on `_Meta` (typically set once on
    a shared base class), instead of a subclass's own `Meta` clobbering them outright. A list
    attribute is concatenated, a dict attribute is merged (the subclass's own entries win on key
    conflicts) - `_Meta` is a normal class attribute, so it's inherited the ordinary way and
    doesn't need repeating on every subclass.

        class BaseModelSerializer(MetaCombiningMixin, ModelSerializer):
            meta_fields_to_combine = ["relational_fields"]

            class _Meta:
                relational_fields = {"owner": relational_serializer(OwnerSerializer)}

        class WidgetSerializer(BaseModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name", "owner", "tags"]
                relational_fields = {"tags": relational_serializer(TagSerializer)}
            # WidgetSerializer.Meta.relational_fields == {"owner": ..., "tags": ...}
    """

    meta_fields_to_combine = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        base_meta = getattr(cls, "_Meta", None)
        # cls.__dict__, not getattr(cls, "Meta") - a subclass that doesn't declare its own Meta
        # inherits the parent's Meta object by identity, and combining into that would mutate the
        # already-combined parent in place (and, for a concatenated list, duplicate its entries).
        own_meta = cls.__dict__.get("Meta")
        if own_meta is None:
            return
        for name in cls.meta_fields_to_combine:
            base_value = getattr(base_meta, name, None)
            if base_value is None:
                continue
            own_value = getattr(own_meta, name, None)
            empty = type(base_value)()
            setattr(own_meta, name, cls._combine(own_value if own_value is not None else empty, base_value))

    @staticmethod
    def _combine(own, base):
        if isinstance(base, list):
            return base + own
        if isinstance(base, dict):
            return {**base, **own}
        raise TypeError("meta_fields_to_combine only supports list or dict attributes")
