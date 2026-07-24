class RequestContextMixin:
    """Small helpers for reaching the current request/user from inside serializer logic
    (validators, SerializerMethodField getters, get_extra_kwargs, etc.) - context isn't always at hand."""

    def current_request(self):
        return self.context.get("request")

    def current_user(self):
        request = self.current_request()
        return request.user if request and request.user.is_authenticated else None
