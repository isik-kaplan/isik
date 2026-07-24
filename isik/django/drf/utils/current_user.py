from rest_framework.serializers import HiddenField


class CurrentUser:
    """
    Resolves to the request's authenticated user, or None with no request or an anonymous one.

    This is just a `default` - DRF only calls a default when the field is missing from input,
    so pairing it with a regular writable field wouldn't stop a client from overriding it. Use
    `CurrentUserField` instead to actually enforce it.

    `create_only=True` only resolves to the current user while creating - on update it keeps
    whatever the instance already has, so nothing can reassign it afterward.
    """

    requires_context = True

    def __init__(self, create_only=False):
        self.create_only = create_only

    def __call__(self, serializer_field):
        instance = serializer_field.parent.instance
        if self.create_only and instance is not None:
            return getattr(instance, serializer_field.source)
        request = serializer_field.context.get("request")
        return request.user if request and request.user.is_authenticated else None

    def __repr__(self):
        return f"{self.__class__.__name__}(create_only={self.create_only})"


class CurrentUserField(HiddenField):
    """
    A HiddenField defaulting to CurrentUser - HiddenField.get_value() ignores input entirely,
    so this is what actually enforces it: a client can never supply or override this field.

        owner = CurrentUserField()
        created_by = CurrentUserField(create_only=True)
    """

    def __init__(self, create_only=False, **kwargs):
        kwargs.setdefault("default", CurrentUser(create_only=create_only))
        super().__init__(**kwargs)
