from collections.abc import Mapping
from operator import attrgetter

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import AND, NOT, OR, SAFE_METHODS, BasePermission, BasePermissionMetaclass


class ReadOnly(BasePermission):
    """
    Meant to be combined with DRF's `&`/`|` permission operators rather than used alone - e.g.
    `IsSuperUser | (IsAuthenticated & ReadOnly)` for "superusers can write, everyone else can only
    read".
    """

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


def is_owner(owner_field, of=None):
    """
    Creates an object-level permission that allows access only if request.user
    is the value of obj.<owner_field> - or, with `of`, if request.user.<of> is: a row owned by a
    tenant rather than a person, e.g. `is_owner("tenant", of="organization")` compares
    obj.tenant to request.user.organization. `of` may be a dotted path.

    Object-level only: DRF never consults object permissions for `list`/`create`, so used alone
    as a view permission this allows the whole collection. Pair it with a request-level
    permission, or scope it with `guarding(..., actions=[...])` to actions that have an object.
    """
    owner_of = attrgetter(of) if of else None

    def has_object_permission(self, request, view, obj):  # NOQA
        owner = getattr(obj, owner_field, None)
        if owner_of is None:
            return bool(owner and owner == request.user)
        try:
            expected = owner_of(request.user)
        except AttributeError:
            # e.g. AnonymousUser, or a user with no organization to own anything through.
            return False
        return bool(owner and owner == expected)

    name = f"IsOwnerPermission(owner_field={owner_field}, of={of})"
    if not of:
        name = f"IsOwnerPermission(owner_field={owner_field})"  # unchanged for existing callers
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


def object_property(property_=None, attribute=None):
    """
    Creates an object-level permission from a boolean property or attribute on the object - the
    `user_property` counterpart for the row being acted on rather than the user acting. Exactly one
    of property_ or attribute must be provided.

    Mostly useful negated, inside `guarding` - which is what makes `~` safe on an object-level
    predicate at all (see `guarding`):

        guarding(~object_property(attribute="is_app"), actions=["update", "partial_update", "destroy"])
    """
    if (property_ is None) == (attribute is None):
        raise ValueError("object_property requires exactly one of property_ or attribute")

    getter = property_.fget if property_ else attrgetter(attribute)

    def has_object_permission(self, request, view, obj):  # NOQA
        try:
            return bool(getter(obj))
        except AttributeError:
            return False

    property_name = property_.fget.__name__ if property_ else attribute
    name = f"ObjectAttributePermission(property={property_name})"
    bases = (BasePermission,)
    attrs = dict(
        message=_(f"Object property {property_name} is False"),
        has_object_permission=has_object_permission,
    )
    return type(name, bases, attrs)


def _object_only(permission):
    # A leaf that never overrides has_permission has nothing to say without an object.
    return type(permission).has_permission is BasePermission.has_permission


def _evaluate(permission, request, view, obj=None):
    """
    Evaluates a DRF permission tree (`&`/`|`/`~` over permission instances) as one predicate.

    DRF's own operators evaluate `has_permission` and `has_object_permission` as two separate
    trees, so `~` on an object-level permission is broken there: `~is_owner(...)` negates the
    default `has_permission` of True and refuses every request before any object is seen. Here a
    leaf's value is both halves together, and negation applies to that.

    With an object that is plain boolean logic. Without one (`create`, `list`, a non-detail action)
    an object-only leaf is unknown, not true, and the operators use three-valued logic - so `None`
    means "only answerable against a row, and there is none".
    """
    if isinstance(permission, NOT):
        value = _evaluate(permission.op1, request, view, obj)
        return None if value is None else not value
    if isinstance(permission, (AND, OR)):
        left = _evaluate(permission.op1, request, view, obj)
        right = _evaluate(permission.op2, request, view, obj)
        decisive = isinstance(permission, OR)  # the value that settles it on either side
        if left is decisive or right is decisive:
            return decisive
        if left is None or right is None:
            return None
        return not decisive
    if obj is None:
        if _object_only(permission):
            return None
        return bool(permission.has_permission(request, view))
    return bool(permission.has_permission(request, view) and permission.has_object_permission(request, view, obj))


class _GuardMetaclass(BasePermissionMetaclass):
    """A guard is read off `permission_classes` by the viewset that runs it, so it has to stay a top-level
    entry - composing it would hide it from that viewset, and invert what its out-of-scope True means."""

    def _refuse(cls, *args):
        raise TypeError(
            f"{cls.__name__} can't be combined with &, | or ~ - compose the predicate inside guarding() instead."
        )

    __and__ = __or__ = __rand__ = __ror__ = __invert__ = _refuse


class Guard(BasePermission, metaclass=_GuardMetaclass):
    """Base of every class `guarding()` builds - see there."""

    predicate = None
    fields = None  # {field name: target value, or `ANY_VALUE`}
    actions = None

    def __init__(self):
        self.predicate_instance = self.predicate()

    def allows(self, request, view, obj=None):
        """False only when the predicate definitely refuses - see `_evaluate` for the unknown case."""
        return _evaluate(self.predicate_instance, request, view, obj) is not False

    def in_scope(self, view):
        return self.actions is not None and view.action in self.actions

    def has_permission(self, request, view):
        if not hasattr(view, "action"):
            raise ImproperlyConfigured(f"{type(self).__name__} needs a viewset - {type(view).__name__} has no action.")
        if self.fields is not None:
            if not getattr(view, "runs_field_guards", False):  # pragma: no mutate
                # Answering True here would permit everything and say nothing, since
                # permission_classes are ANDed - fail closed and loud instead.
                raise ImproperlyConfigured(
                    f"{type(view).__name__} declares {type(self).__name__} but doesn't run field guards - "
                    "add GuardedFieldsMixin (part of BaseModelViewSet)."
                )
            return True
        return not self.in_scope(view) or self.allows(request, view)

    def has_object_permission(self, request, view, obj):
        return not self.in_scope(view) or self.allows(request, view, obj)


ANY_VALUE = object()


def guarding(predicate, fields=None, actions=None, message=None):
    """
    Narrows a permission - anything a `permission_classes` entry can be, `&`/`|`/`~` compositions
    included - to some actions or some fields. Exactly one of `fields`/`actions`:

        permission_classes = [
            MayIssuePublicInvitations,
            guarding(is_owner("issued_by"), fields=["names_issuer"]),
            guarding(is_owner("created_by") | IsSuperUser, actions=["partial_update", "destroy"]),
        ]

    `actions=`: the predicate applies only when `view.action` is one of them, and refuses with a
    403 like any permission. Runs where DRF runs permissions, so it needs nothing else.

    `fields=`: the predicate applies only to a write that changes one of those fields, and refuses
    with a `ValidationError` keyed by the field - a form can show it beside the control, the way
    every other field-level refusal arrives. It can't run where DRF runs permissions: the payload
    isn't coerced yet (a form-encoded "false" is a truthy string) and "is this a change?" needs the
    typed value against the stored one - so `GuardedFieldsMixin` (part of `BaseModelViewSet`) runs
    it after validation, and echoing a field back unchanged is not a change. On a viewset without
    that mixin it raises `ImproperlyConfigured` on every request rather than permit silently.

    `fields=` also takes a dict of target values, for a field only dangerous in one direction -
    `fields={"names_issuer": True}` guards turning it on, and leaves turning it off to anyone.

    The predicate is evaluated as one expression over the request and the object, so `~` works on
    an object-level permission (it doesn't under DRF's own operators). Where there is no object -
    `create`, `list`, a non-detail action - an object-only part of it is unknown, and a guard whose
    answer is unknown allows: `is_owner` has nothing to hold against a row that doesn't exist yet.

    A guard must stay a top-level `permission_classes` entry; composing one raises `TypeError`.
    """
    if (fields is None) == (actions is None):
        raise ValueError("guarding requires exactly one of fields or actions")
    if not (fields or actions):
        raise ValueError("guarding requires at least one field or action")
    if isinstance(fields, str) or isinstance(actions, str):
        raise ValueError("guarding takes a list of fields or actions, not a single string")
    if fields is not None:
        fields = dict(fields) if isinstance(fields, Mapping) else dict.fromkeys(fields, ANY_VALUE)
        scope = f"fields={sorted(fields)}"
        default_message = _("You may not change this field.")
    else:
        actions = frozenset(actions)
        scope = f"actions={sorted(actions)}"
        default_message = _("You may not perform this action.")

    predicate_name = getattr(predicate, "__name__", type(predicate).__name__)
    attrs = dict(
        predicate=predicate,
        fields=fields,
        actions=actions,
        message=message or default_message,
    )
    return _GuardMetaclass(f"Guarding({predicate_name}, {scope})", (Guard,), attrs)
