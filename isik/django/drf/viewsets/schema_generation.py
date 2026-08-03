from functools import wraps


def none_during_schema_generation(method):
    """
    Decorator for a viewset's `get_queryset()` (or any method that assumes a real, authenticated
    request) that returns `self.model.objects.none()` instead of running the wrapped method during
    drf-spectacular/drf-yasg schema generation - `self.request.user` is `AnonymousUser` at that
    point, not a real user, so a `get_queryset()` filtering by the current user would otherwise
    crash or misbehave while the schema is being built.

    A decorator rather than a mixin, deliberately - apply it to just the one method that needs it:

        class OrganizationViewSet(BaseModelViewSet):
            @none_during_schema_generation
            def get_queryset(self):
                return super().get_queryset().filter(members__user=self.request.user)

    Assumes `self.model` is set (e.g. via `RequiredAttributesMixin`, as `BaseModelViewSet` already
    requires).
    """

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if getattr(self, "swagger_fake_view", False):
            return self.model.objects.none()
        return method(self, *args, **kwargs)

    return wrapper
