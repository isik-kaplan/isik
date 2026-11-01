from functools import wraps

from isik.common.utils.sentinel import Sentinel


_EMPTY_QUERYSET = Sentinel("_EMPTY_QUERYSET")


def none_during_schema_generation(method=None, *, answer=_EMPTY_QUERYSET):
    """
    Decorator for a viewset's `get_queryset()` (or any method that assumes a real, authenticated
    request) that answers without running the wrapped method during drf-spectacular/drf-yasg schema
    generation - `self.request.user` is `AnonymousUser` at that point, not a real user, so a
    `get_queryset()` filtering by the current user would otherwise crash or misbehave while the
    schema is being built.

    A decorator rather than a mixin, deliberately - apply it to just the one method that needs it:

        class OrganizationViewSet(BaseModelViewSet):
            @none_during_schema_generation
            def get_queryset(self):
                return super().get_queryset().filter(members__user=self.request.user)

    Bare, it answers `self.model.objects.none()` - right for `get_queryset()`, and it assumes
    `self.model` is set (e.g. via `RequiredAttributesMixin`, as `BaseModelViewSet` already requires).
    A method returning something else says what with `answer=`: a value, or `f(view)` for one made
    per call:

            @none_during_schema_generation(answer=lambda view: Organization.objects.none())
            def administered(self):
                return Organization.objects.filter(admins=self.request.user)
    """

    def decorate(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            if getattr(self, "swagger_fake_view", None):
                if answer is _EMPTY_QUERYSET:
                    return self.model.objects.none()
                return answer(self) if callable(answer) else answer
            return method(self, *args, **kwargs)

        return wrapper

    return decorate if method is None else decorate(method)
