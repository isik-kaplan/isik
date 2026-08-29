from operator import attrgetter

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import SAFE_METHODS, BasePermission


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class IsAnonymous(BasePermission):
    """Allows access only to unauthenticated users."""

    def has_permission(self, request, view):
        return not request.user.is_authenticated


class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class IsAuthenticatedANDSignupCompleted(BasePermission):
    """
    Allows access only to authenticated users who have completed signup.
    The user model must define SIGNUP_COMPLETED_FIELD, naming the boolean field to check - raises
    ImproperlyConfigured (not a bare AttributeError) if the user model never defines it.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        try:
            signup_completed_field = user.SIGNUP_COMPLETED_FIELD
        except AttributeError as exc:
            raise ImproperlyConfigured(
                f"{user.__class__.__name__} must define SIGNUP_COMPLETED_FIELD to use {self.__class__.__name__}."
            ) from exc
        return bool(getattr(user, signup_completed_field, False))  # pragma: no mutate


def is_owner(owner_field):
    """
    Creates an object-level permission that allows access only if request.user
    is the value of obj.<owner_field>.
    """

    def has_object_permission(self, request, view, obj):  # NOQA
        owner = getattr(obj, owner_field, None)
        return bool(owner and owner == request.user)

    name = f"IsOwnerPermission(owner_field={owner_field})"
    bases = (BasePermission,)
    attrs = dict(
        message=_("User is not the owner of the object"),
        has_object_permission=has_object_permission,
    )
    return type(name, bases, attrs)


def prevent_actions(*actions):
    """
    Creates a permission that denies the given view actions.
    Default action values for a ModelViewSet: "create", "list", "retrieve", "update", "partial_update", "destroy".
    """

    def has_permission(self, request, view):  # NOQA
        return view.action not in actions

    name = f"PreventActionsPermission(actions={actions})"
    bases = (BasePermission,)
    attrs = dict(
        message=_(f"Actions should not be: {actions}"),
        has_permission=has_permission,
    )
    return type(name, bases, attrs)


def user_property(property_=None, attribute=None):
    """
    Creates a permission from a boolean property or attribute on the user model.
    Exactly one of property_ or attribute must be provided.

    If the resolved value has a `.reason` attribute, it's used as the denial message.

    Example:
        user_property(property_=User.is_verified)
        user_property(attribute="is_verified")
    """
    if (property_ is None) == (attribute is None):
        raise ValueError("user_property requires exactly one of property_ or attribute")

    getter = property_.fget if property_ else attrgetter(attribute)

    def has_permission(self, request, view):  # NOQA
        try:
            has_perm = getter(request.user)
        except AttributeError:
            # e.g. request.user is AnonymousUser and doesn't have the attribute/property at all.
            return False
        if hasattr(has_perm, "reason"):
            self.message = has_perm.reason
        return has_perm

    def has_object_permission(self, request, view, obj):  # NOQA
        try:
            has_perm = getter(request.user)
        except AttributeError:
            return False
        if hasattr(has_perm, "reason"):
            self.message = has_perm.reason
        return has_perm

    property_name = property_.fget.__name__ if property_ else attribute
    name = f"UserAttributePermission(property={property_name})"
    bases = (BasePermission,)
    attrs = dict(
        message=_(f"User property {property_name} is False"),
        has_permission=has_permission,
        has_object_permission=has_object_permission,
    )
    return type(name, bases, attrs)
