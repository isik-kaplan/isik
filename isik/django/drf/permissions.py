import functools
from collections.abc import Mapping
from enum import Enum
from operator import attrgetter

from django.core.exceptions import ImproperlyConfigured
from django.db.models.fields.related_descriptors import (
    ForwardManyToOneDescriptor,
    ManyToManyDescriptor,
    ReverseManyToOneDescriptor,
    ReverseOneToOneDescriptor,
)
from django.db.models.query_utils import DeferredAttribute
from django.utils.functional import Promise, lazy
from django.utils.functional import cached_property as django_cached_property
from rest_framework.permissions import (
    AND,
    NOT,
    OR,
    SAFE_METHODS,
    BasePermission,
    BasePermissionMetaclass,
    OperandHolder,
    SingleOperandHolder,
)

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy, lazy_format, translate_text
from isik.common.utils.functional import with_attrs
from isik.common.utils.strings import words_to_pascal


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
                _("%(user_model)s must define SIGNUP_COMPLETED_FIELD to use %(permission)s.")
                % {"user_model": user.__class__.__name__, "permission": self.__class__.__name__}
            ) from exc
        return bool(getattr(user, signup_completed_field, False))  # pragma: no mutate


def _accessor(spec):
    """A dotted name becomes an attrgetter; a callable is used as it is."""
    return spec if callable(spec) else attrgetter(spec)


def _describe(spec):
    return spec if isinstance(spec, str) else getattr(spec, "__name__", type(spec).__name__)


def _joined(names):
    return "And".join(words_to_pascal(name) for name in names)


def is_owner(owner_field, of=None, name=None):
    """
    Creates an object-level permission that allows access only if request.user
    is the value of obj.<owner_field> - or, with `of`, if request.user.<of> is: a row owned by a
    tenant rather than a person, e.g. `is_owner("tenant", of="organization")` compares
    obj.tenant to request.user.organization.

    Both halves take a dotted path or a callable, for an owner that is relations away or computed:

        is_owner("installation.organization", of="profile.organization")
        is_owner(lambda obj: obj.team.lead)
        is_owner("tenant", of=lambda user: user.team)

    A path that breaks partway (a missing attribute, a None relation) means "not the owner".

    This is an equality test - is this object the caller's. "Is it among the organizations the
    caller may administer" is membership, not ownership: write an ordinary permission class for it,
    which `guarding` takes just as well.

    Object-level only: DRF never consults object permissions for `list`/`create`, so used alone
    as a view permission this allows the whole collection. Pair it with a request-level
    permission, or scope it with `guarding(..., actions=[...])` to actions that have an object.

    The class is named from its arguments - `IsOwnerByIssuedBy`, `IsOwnerByTenantOfOrganization` -
    or by `name=`.
    """
    owner_of_obj = _accessor(owner_field)
    owner_of_user = _accessor(of) if of else None

    def has_object_permission(self, request, view, obj):  # NOQA
        try:
            owner = owner_of_obj(obj)
            # e.g. AnonymousUser, or a user with no organization to own anything through.
            expected = owner_of_user(request.user) if owner_of_user else request.user
        except AttributeError:
            return False
        return bool(owner and owner == expected)

    if name is None:
        name = f"IsOwnerBy{words_to_pascal(_describe(owner_field))}"
        if of:
            name += f"Of{words_to_pascal(_describe(of))}"
    bases = (BasePermission,)
    attrs = dict(
        message=gettext_lazy("User is not the owner of the object"),
        has_object_permission=has_object_permission,
    )
    return type(name, bases, attrs)


def prevent_actions(*actions, name=None):
    """
    Creates a permission that denies the given view actions.
    Default action values for a ModelViewSet: "create", "list", "retrieve", "update", "partial_update", "destroy".

    The class is named from the actions - `PreventCreateAndDestroy` - or by `name=`.
    """

    def has_permission(self, request, view):  # NOQA
        return view.action not in actions

    name = name or f"Prevent{_joined(actions)}"
    bases = (BasePermission,)
    attrs = dict(
        message=lazy_format("Actions should not be: %(actions)s", actions=actions),
        has_permission=has_permission,
    )
    return type(name, bases, attrs)


def descriptor_attribute_name(descriptor):
    """
    The instance attribute a class-level descriptor reads - `User.is_staff` -> "is_staff",
    `User.reports` -> "reports" - so it can always be read back with attrgetter: uniform across
    kinds, and a cached_property keeps its cache rather than being bypassed through its raw function.
    """
    if isinstance(descriptor, str):
        return descriptor
    if isinstance(descriptor, property):
        return descriptor.fget.__name__
    if isinstance(descriptor, functools.cached_property):
        return descriptor.attrname
    if isinstance(descriptor, django_cached_property):
        return descriptor.name
    if isinstance(descriptor, DeferredAttribute):
        return descriptor.field.attname  # `owner_id` stays `owner_id`, not the `owner` relation
    if isinstance(descriptor, ForwardManyToOneDescriptor):  # forward one-to-one included
        return descriptor.field.name
    # A reverse relation's `.field` is the field on the *other* model - its name is the wrong answer.
    if isinstance(descriptor, ManyToManyDescriptor):
        return descriptor.rel.get_accessor_name() if descriptor.reverse else descriptor.field.name
    if isinstance(descriptor, ReverseManyToOneDescriptor):
        return descriptor.rel.get_accessor_name()
    if isinstance(descriptor, ReverseOneToOneDescriptor):
        return descriptor.related.get_accessor_name()
    raise TypeError(
        _(
            "Can't tell which attribute %(descriptor)r reads - pass its name as a string instead. "
            "(A plain class attribute is its value, not a descriptor, so it has no name to find.)"
        )
        % {"descriptor": descriptor}
    )


def _resolve_attribute(caller, property_, attribute):
    if (property_ is None) == (attribute is None):
        raise ValueError(_("%(caller)s requires exactly one of property_ or attribute") % {"caller": caller})
    return descriptor_attribute_name(property_ if property_ is not None else attribute)


def only_actions(*actions, name=None):
    """
    Creates a permission that allows only the given view actions - the complement of
    `prevent_actions`, for a viewset that should answer to a few actions and refuse the rest:

        permission_classes = [IsAuthenticated, only_actions("list", "retrieve")]

    The class is named from the actions - `OnlyListAndRetrieve` - or by `name=`.
    """
    if not actions:
        raise ValueError(_("only_actions requires at least one action - with none it would refuse everything"))

    def has_permission(self, request, view):  # NOQA
        return view.action in actions

    name = name or f"Only{_joined(actions)}"
    bases = (BasePermission,)
    attrs = dict(
        message=lazy_format("Actions should be one of: %(actions)s", actions=actions),
        has_permission=has_permission,
    )
    return type(name, bases, attrs)


DEFAULT_PERMISSION_MESSAGE = gettext_lazy("You need the %(permission)s permission to do this.")


def _permission_name(permission):
    if isinstance(permission, Enum) and not isinstance(permission, str):
        permission = permission.value
    if not isinstance(permission, str):
        raise TypeError(
            _("django_permission takes a permission name, or an Enum member whose value is one - not %(permission)r")
            % {"permission": permission}
        )
    if not permission:
        raise ValueError(_("django_permission needs a permission name, not an empty string"))
    return permission


def _render_permission_message(template, permission):
    # A lazy template (gettext_lazy) is already translated when rendered; a plain string is looked up
    # in the catalogs as it is, which is harmless when it isn't in them.
    text = str(template) if isinstance(template, Promise) else translate_text(template)
    return text % {"permission": permission} if "%(" in text else text


def django_permission(permission, message=None, name=None):
    """
    Creates a permission that allows the request only if the caller holds a Django permission -
    `request.user.has_perm(permission)`, answered by whichever authentication backends the project
    runs, so the name is whatever format they understand (`"app_label.codename"` for Django's own
    ModelBackend). Takes a string - a `str` subclass or a StrEnum/TextChoices member included - or an
    Enum member whose value is one:

        permission_classes = [django_permission("invitations.issue_invitation")]
        permission_classes = [django_permission(Permission.INVITATIONS_ISSUE_PUBLIC_INVITATION) | IsSuperUser]
        guarding(django_permission("mail.edit_smtp_settings"), fields=["smtp_password"])

    Request-level only. `user.has_perm(permission, obj)` is real Django, but ModelBackend answers False
    to every object-level check, so passing the object through would refuse everything on a project
    whose backend doesn't implement them - and refuse it silently, looking like an ownership failure.
    With ModelBackend an active superuser always passes and an inactive user never does. An
    unauthenticated request is refused without asking the backend.

    `message=` is what a refused request is told, in one of three forms - all translatable:

    - a template with an optional `%(permission)s` placeholder, filled with the permission's name.
      Pass it through `gettext_lazy` to have it translated; write a literal `%` as `%%` when the
      template has a placeholder:

          django_permission(Permission.X, message=gettext_lazy("Only %(permission)s holders can do this."))

    - a fixed message - a template without a placeholder: `message=gettext_lazy("Ask an admin.")`
    - a callable, called when a request is refused as `message(permission=..., request=..., view=...)`
      and returning the message (translate it inside):

          def refusal(permission, request, view):
              return gettext("%(user)s can't do that here.") % {"user": request.user}

    Without `message=`, the default names the permission: "You need the <permission> permission to do
    this." - which tells a refused caller your permission names; pass a message if they shouldn't know.

    The class is named from the permission - `HasInvitationsIssuePublicInvitation` - or by `name=`.
    """
    permission = _permission_name(permission)
    if message is None:
        message = DEFAULT_PERMISSION_MESSAGE
    elif not (callable(message) or isinstance(message, (str, Promise))):
        raise TypeError(
            _("django_permission's message= takes a template, a message or a callable - not %(message)r")
            % {"message": message}
        )
    template = DEFAULT_PERMISSION_MESSAGE if callable(message) else message

    def has_permission(self, request, view):  # NOQA
        user = getattr(request, "user", None)
        allowed = bool(user and user.is_authenticated and user.has_perm(permission))
        if not allowed and callable(message):
            self.message = message(permission=permission, request=request, view=view)
        return allowed

    name = name or f"Has{words_to_pascal(str(permission))}"
    bases = (BasePermission,)
    attrs = dict(
        permission=permission,
        message=lazy(_render_permission_message, str)(template, permission),
        has_permission=has_permission,
    )
    return type(name, bases, attrs)


def user_property(property_=None, attribute=None, name=None):
    """
    Creates a permission from a boolean attribute of the user - named, or given as the model's own
    descriptor, which a rename carries along and a typo can't survive:

        user_property(User.is_verified)   # a property
        user_property(User.is_staff)      # a model field
        user_property(User.quota)         # a cached_property (functools' or Django's)
        user_property(User.profile)       # a relation, forward or reverse
        user_property("is_verified")      # a name - dotted paths allowed

    `property_=`/`attribute=` still work as keywords. A plain class attribute (`is_app = True`) has
    to be passed by name: `User.is_app` is just `True`. And a descriptor doesn't prove which model it
    came from - `user_property(Widget.is_active)` reads `request.user.is_active`.

    If the resolved value has a `.reason` attribute, it's used as the denial message.

    The class is named from the attribute - `UserIsVerified` - or by `name=`.
    """
    property_name = _resolve_attribute("user_property", property_, attribute)
    getter = attrgetter(property_name)

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

    name = name or f"User{words_to_pascal(property_name)}"
    bases = (BasePermission,)
    attrs = dict(
        message=lazy_format("User property %(property)s is False", property=property_name),
        has_permission=has_permission,
        has_object_permission=has_object_permission,
    )
    return type(name, bases, attrs)


def object_property(property_=None, attribute=None, name=None):
    """
    Creates an object-level permission from a boolean attribute of the object - the
    `user_property` counterpart for the row being acted on rather than the user acting. Takes a
    name or a descriptor exactly as `user_property` does. Named from the attribute - `ObjectIsApp` -
    or by `name=`.

    Mostly useful negated, inside `guarding` - which is what makes `~` safe on an object-level
    predicate at all (see `guarding`):

        guarding(~object_property(attribute="is_app"), actions=["update", "partial_update", "destroy"])
    """
    property_name = _resolve_attribute("object_property", property_, attribute)
    getter = attrgetter(property_name)

    def has_object_permission(self, request, view, obj):  # NOQA
        try:
            return bool(getter(obj))
        except AttributeError:
            return False

    name = name or f"Object{words_to_pascal(property_name)}"
    bases = (BasePermission,)
    attrs = dict(
        message=lazy_format("Object property %(property)s is False", property=property_name),
        has_object_permission=has_object_permission,
    )
    return type(name, bases, attrs)


def _object_only(permission):
    # A leaf that never overrides has_permission has nothing to say without an object.
    return type(permission).has_permission is BasePermission.has_permission


def evaluate_permission(permission, request, view, obj=None):
    """
    Evaluates a DRF permission tree (`&`/`|`/`~` over permission instances) as one predicate:
    True, False, or None for "unknown" - see below. What a guard asks its predicate with.

    DRF's own operators evaluate `has_permission` and `has_object_permission` as two separate
    trees, so `~` on an object-level permission is broken there: `~is_owner(...)` negates the
    default `has_permission` of True and refuses every request before any object is seen. Here a
    leaf's value is both halves together, and negation applies to that.

    With an object that is plain boolean logic. Without one (`create`, `list`, a non-detail action)
    an object-only leaf is unknown, not true, and the operators use three-valued logic - so `None`
    means "only answerable against a row, and there is none".
    """
    if isinstance(permission, NOT):
        value = evaluate_permission(permission.op1, request, view, obj)
        return None if value is None else not value
    if isinstance(permission, (AND, OR)):
        left = evaluate_permission(permission.op1, request, view, obj)
        right = evaluate_permission(permission.op2, request, view, obj)
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
            _("%(guard)s can't be combined with &, | or ~ - compose the predicate inside guarding() instead.")
            % {"guard": cls.__name__}
        )

    __and__ = __or__ = __rand__ = __ror__ = __invert__ = _refuse


class Guard(BasePermission, metaclass=_GuardMetaclass):
    """Base of every class `guarding()` builds - see there."""

    predicate = None
    fields = None  # {field name: a target - see `_as_target`}
    actions = None

    def __init__(self):
        self.predicate_instance = self.predicate()

    def allows(self, request, view, obj=None):
        """False only when the predicate definitely refuses - see `evaluate_permission` for the unknown case."""
        return evaluate_permission(self.predicate_instance, request, view, obj) is not False

    def in_scope(self, view):
        return self.actions is not None and view.action in self.actions

    def has_permission(self, request, view):
        if not hasattr(view, "action"):
            raise ImproperlyConfigured(
                _("%(guard)s needs a viewset - %(view)s has no action.")
                % {"guard": type(self).__name__, "view": type(view).__name__}
            )
        if self.fields is not None:
            if not getattr(view, "runs_field_guards", False):  # pragma: no mutate
                # Answering True here would permit everything and say nothing, since
                # permission_classes are ANDed - fail closed and loud instead.
                raise ImproperlyConfigured(
                    _(
                        "%(view)s declares %(guard)s but doesn't run field guards - "
                        "add GuardedFieldsMixin (part of BaseModelViewSet)."
                    )
                    % {"view": type(view).__name__, "guard": type(self).__name__}
                )
            return True
        return not self.in_scope(view) or self.allows(request, view)

    def has_object_permission(self, request, view, obj):
        return not self.in_scope(view) or self.allows(request, view, obj)


class GuardTarget:
    """
    Which incoming values of a guarded field need the permission - a `setting=` target. Subclass it
    and implement `matches(incoming)` for a target no literal can express (or use `guarding.matching`);
    a plain value becomes `EqualsTarget`, `guarding.values(...)` a `OneOfTarget`,
    `guarding.other_than(...)` a `NotOneOfTarget`, and a flat `fields=` list `ANY_VALUE`.
    """

    def matches(self, incoming):
        raise NotImplementedError


class AnyValueTarget(GuardTarget):
    def matches(self, incoming):
        return True

    def __repr__(self):
        return "ANY_VALUE"


class EqualsTarget(GuardTarget):
    def __init__(self, value):
        self.value = value

    def matches(self, incoming):
        return incoming == self.value

    def __repr__(self):
        return repr(self.value)


class OneOfTarget(GuardTarget):
    def __init__(self, values):
        self.values = values

    def matches(self, incoming):
        # equality rather than `in` a set, so an unhashable target still works
        return any(incoming == value for value in self.values)

    def __repr__(self):
        return f"guarding.values{self.values!r}"


class NotOneOfTarget(GuardTarget):
    def __init__(self, values):
        self.values = values

    def matches(self, incoming):
        return not any(incoming == value for value in self.values)

    def __repr__(self):
        return f"guarding.other_than{self.values!r}"


class MatchingTarget(GuardTarget):
    def __init__(self, predicate):
        self.predicate = predicate

    def matches(self, incoming):
        return bool(self.predicate(incoming))

    def __repr__(self):
        return f"guarding.matching({getattr(self.predicate, '__name__', self.predicate)!r})"


ANY_VALUE = AnyValueTarget()


def _as_target(value):
    return value if isinstance(value, GuardTarget) else EqualsTarget(value)


def guarding_values(*values):
    """
    A `setting=` target matching any of several values - usually spelled `guarding.values(...)`:

        guarding(is_owner("issued_by"), setting={"status": guarding.values(APPROVED, FEATURED)})

    A bare set would be ambiguous with a field whose value legitimately is that set, so it's said
    with a marker instead.
    """
    if not values:
        raise ValueError(_("guarding.values requires at least one value"))
    return OneOfTarget(values)


def guarding_other_than(*values):
    """
    A `setting=` target matching every value except these - usually spelled
    `guarding.other_than(...)`. Guards moving a field *off* a safe value, whatever it moves to:

        guarding(IsSuperUser, setting={"visibility": guarding.other_than(Visibility.PRIVATE)})
    """
    if not values:
        raise ValueError(_("guarding.other_than requires at least one value"))
    return NotOneOfTarget(values)


def guarding_matching(predicate):
    """
    A `setting=` target matching whatever `predicate(incoming)` is truthy for - usually spelled
    `guarding.matching(...)`, for a target no literal can express:

        guarding(IsSuperUser, setting={"max_uses": guarding.matching(lambda uses: uses is None or uses > 100)})
    """
    if not callable(predicate):
        raise TypeError(_("guarding.matching requires a callable"))
    return MatchingTarget(predicate)


def permission_name(predicate):
    """
    A class-style name for anything a `permission_classes` entry can be, compositions included:
    `~A` -> `NotA`, `A | B` -> `AOrB`, `A & ~B` -> `AAndNotB`. What `guarding` names its classes with.
    """
    if isinstance(predicate, SingleOperandHolder):
        return f"Not{permission_name(predicate.op1_class)}"
    if isinstance(predicate, OperandHolder):
        joiner = "And" if predicate.operator_class is AND else "Or"
        return f"{permission_name(predicate.op1_class)}{joiner}{permission_name(predicate.op2_class)}"
    return predicate.__name__


@with_attrs(values=guarding_values, other_than=guarding_other_than, matching=guarding_matching)
def guarding(predicate, fields=None, setting=None, actions=None, message=None, name=None):
    """
    Narrows a permission - anything a `permission_classes` entry can be, `&`/`|`/`~` compositions
    included - to some fields or some actions. Exactly one of `fields`/`setting`/`actions`:

        permission_classes = [
            MayIssuePublicInvitations,
            guarding(is_owner("issued_by"), fields=["label"]),
            guarding(is_owner("issued_by"), setting={"names_issuer": True}),
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

    `setting=`: the same, but only for a change *to* the given value - for a field dangerous in one
    direction. `setting={"names_issuer": True}` guards turning it on and leaves turning it off to
    anyone. A target is one value; for several, `setting={"status": guarding.values(APPROVED, FEATURED)}`;
    for everything but some, `guarding.other_than(...)`; for anything else, `guarding.matching(callable)`.

    The predicate is evaluated as one expression over the request and the object, so `~` works on
    an object-level permission (it doesn't under DRF's own operators). Where there is no object -
    `create`, `list`, a non-detail action - an object-only part of it is unknown, and a guard whose
    answer is unknown allows: `is_owner` has nothing to hold against a row that doesn't exist yet.

    A guard must stay a top-level `permission_classes` entry; composing one raises `TypeError`.

    The class is named from the predicate and scope - `IsOwnerByIssuedByForSettingNamesIssuer`,
    `NotObjectIsAppForPartialUpdate` - or by `name=`.
    """
    if [fields, setting, actions].count(None) != 2:
        raise ValueError(_("guarding requires exactly one of fields, setting or actions"))
    if not (fields or setting or actions):
        raise ValueError(_("guarding requires at least one field or action"))
    if isinstance(fields, str) or isinstance(actions, str):
        raise ValueError(_("guarding takes a list of fields or actions, not a single string"))
    if isinstance(fields, Mapping):
        raise ValueError(_("guarding's fields= takes field names - for target values use setting="))
    if setting is not None and not isinstance(setting, Mapping):
        raise ValueError(_("guarding's setting= takes a dict of field names to target values"))
    if fields is not None:
        fields = dict.fromkeys(fields, ANY_VALUE)
        scope = _joined(sorted(fields))
        default_message = gettext_lazy("You may not change this field.")
    elif setting is not None:
        fields = {name: _as_target(setting[name]) for name in sorted(setting)}
        scope = f"Setting{_joined(fields)}"
        default_message = gettext_lazy("You may not change this field.")
    else:
        actions = frozenset(actions)
        scope = _joined(sorted(actions))
        default_message = gettext_lazy("You may not perform this action.")

    attrs = dict(
        predicate=predicate,
        fields=fields,
        actions=actions,
        message=message or default_message,
    )
    return _GuardMetaclass(name or f"{permission_name(predicate)}For{scope}", (Guard,), attrs)
