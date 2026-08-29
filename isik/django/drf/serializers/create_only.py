class CreateOnlyFieldsMixin:
    """
    Fields listed in `Meta.create_only_fields` are settable at creation, then forced read-only
    (and not required) on every update after that - set once, then locked.

        class Meta:
            model = Widget
            fields = ["id", "slug", "name"]
            create_only_fields = ["slug"]
    """

    def get_extra_kwargs(self):
        kwargs = super().get_extra_kwargs()
        # get_extra_kwargs() is a ModelSerializer concept - super().get_extra_kwargs() above
        # already requires a Meta, so no need to guard against a missing one here too.
        create_only_fields = getattr(self.Meta, "create_only_fields", None)
        if self.instance and create_only_fields:
            for field in create_only_fields:
                kwargs.setdefault(field, {})
                kwargs[field]["read_only"] = True
        return kwargs
