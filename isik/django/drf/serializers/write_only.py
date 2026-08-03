from django.core.exceptions import ImproperlyConfigured


class WriteOnlyFieldsMixin:
    """
    Fields listed in `Meta.write_only_fields` are settable but never appear in the serialized
    output - the create_only_fields counterpart to write-once-never-read secrets.

        class Meta:
            model = SocialApp
            fields = ["id", "name", "secret"]
            write_only_fields = ["secret"]

    A field name can't appear in both `write_only_fields` and `create_only_fields` on the same
    class - together they'd force both `read_only=True` and `write_only=True` on an update
    request, which DRF's Field itself rejects with an assertion error. Raised as
    `ImproperlyConfigured` at class-definition time instead, so the mistake is caught immediately
    rather than surfacing as an opaque crash on the first PATCH/PUT.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        own_meta = cls.__dict__.get("Meta")
        if own_meta is None:
            return
        overlap = set(getattr(own_meta, "write_only_fields", None) or []) & set(
            getattr(own_meta, "create_only_fields", None) or []
        )
        if overlap:
            raise ImproperlyConfigured(
                f"{cls.__name__}: {sorted(overlap)} can't be in both "
                f"write_only_fields and create_only_fields."
            )

    def get_extra_kwargs(self):
        kwargs = super().get_extra_kwargs()
        # get_extra_kwargs() is a ModelSerializer concept - super().get_extra_kwargs() above
        # already requires a Meta, so no need to guard against a missing one here too.
        write_only_fields = getattr(self.Meta, "write_only_fields", None)
        if write_only_fields:
            for field in write_only_fields:
                kwargs.setdefault(field, {})
                kwargs[field]["write_only"] = True
        return kwargs
