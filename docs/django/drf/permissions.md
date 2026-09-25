# permissions

A grab-bag of DRF `BasePermission`s: a few ready-made classes, plus factories that build a permission class from a callable/property/action list rather than requiring a hand-written subclass for every simple check.

Every factory names its class after what it checks, as a class name rather than the call that made it: `is_owner("issued_by")` is `IsOwnerByIssuedBy`, `user_property("is_verified")` is `UserIsVerified`, `prevent_actions("create", "destroy")` is `PreventCreateAndDestroy`, `guarding(~object_property("is_app"), actions=["destroy"])` is `NotObjectIsAppForDestroy`. Each takes `name=` to choose the name yourself.

## ReadOnly / IsAnonymous / IsSuperUser

Plain permission classes - safe methods only, unauthenticated only, superuser only.

```python
class WidgetViewSet(ModelViewSet):
    permission_classes = [IsSuperUser | ReadOnly]  # write access limited to superusers
```

## IsAuthenticatedANDSignupCompleted

Allows only authenticated users who have completed signup, per a boolean field named on the user model.

```python
class User(AbstractUser):
    SIGNUP_COMPLETED_FIELD = "profile_completed"
```

- Reads `user.SIGNUP_COMPLETED_FIELD` directly (not via `getattr` with a default) - a user model that never defines it raises `ImproperlyConfigured` rather than silently denying access.

## is_owner

Builds an object-level permission allowing access only when `request.user` matches `obj.<owner_field>`. With `of=`, it compares against an attribute of the user instead, for rows owned by a tenant rather than a person. Both halves take a dotted path or a callable.

```python
permission_classes = [is_owner("owner")]
permission_classes = [is_owner("tenant", of="organization")]  # obj.tenant == request.user.organization
permission_classes = [is_owner("installation.organization", of="profile.organization")]
permission_classes = [is_owner(lambda obj: obj.team.lead)]
```

- A path that breaks partway (a missing attribute, a `None` relation) means "not the owner", not an error.
- It stays an equality test: *is this object the caller's*. "Is it among the organizations the caller may administer" is membership, not ownership. Write an ordinary permission class for that; `guarding` takes it just as well.

- Object-level only. DRF never consults object permissions for `list`/`create`, so used alone it allows the whole collection. Pair it with a request-level permission, or scope it with `guarding(..., actions=[...])`.
- It deliberately has no refusing `has_permission`: inside `guarding`, "no object yet" has to read as unknown, not as refused.

## prevent_actions

Builds a permission denying the given viewset actions (`view.action`), e.g. to block `destroy` on an otherwise-writable viewset.

```python
permission_classes = [prevent_actions("destroy", "create")]
```

## object_property

The `user_property` counterpart for the row being acted on: builds an object-level permission from a boolean attribute of the object, named or given as a descriptor exactly like `user_property`. Mostly useful negated inside `guarding`, which is what makes `~` work on it.

```python
guarding(~object_property(attribute="is_app"), actions=["update", "partial_update", "destroy"])
```

## only_actions

The complement of `prevent_actions`: builds a permission allowing only the given actions and refusing every other. Requires at least one action, since with none it would refuse everything.

```python
permission_classes = [IsAuthenticated, only_actions("list", "retrieve")]  # OnlyListAndRetrieve
```

## user_property

Builds a permission from a boolean attribute of the user. Pass it by name, or as the model's own descriptor, which a rename carries along and a typo can't survive. If the resolved value has a `.reason` attribute, it becomes the permission's denial `.message`.

```python
user_property(User.is_verified)   # a property
user_property(User.is_staff)      # a model field
user_property(User.quota)         # a cached_property (functools' or Django's), cache kept
user_property(User.profile)       # a relation, forward or reverse
user_property("is_verified")      # a name, dotted paths allowed
```

- A descriptor resolves to the attribute *name*, which is then read off the instance. A reverse relation resolves to its accessor, not to the field on the other model.
- `property_=`/`attribute=` still work as keywords, and exactly one must be given.
- A plain class attribute (`is_app = True`) has to be passed by name: `User.is_app` is just `True`, so there is nothing to resolve, and it raises `TypeError`.
- A descriptor doesn't prove which model it came from: `user_property(Widget.is_active)` reads `request.user.is_active`.

## guarding

Narrows a permission, including any `&`/`|`/`~` composition, to some actions or some fields. It wraps the predicate rather than being a factory parameter, so it works for plain classes like `IsSuperUser` and for composed predicates alike. Pass exactly one of `fields`/`setting`/`actions`.

```python
permission_classes = [
    MayIssuePublicInvitations,
    guarding(is_owner("issued_by"), setting={"names_issuer": True}),
    guarding(is_owner("created_by") | IsSuperUser, actions=["partial_update", "rotate_secret"]),
    guarding(~object_property(attribute="is_app"), actions=["partial_update"]),
]
```

- **`actions=`**: the predicate applies only when `view.action` is listed, and refuses with a 403. It runs where DRF runs permissions, so it needs nothing else.
- **`fields=`**: the predicate applies only to a write that changes a listed field, and refuses with a `ValidationError` keyed by that field, so a form can render it beside the control. `GuardedFieldsMixin` (part of `BaseModelViewSet`, see [viewsets/guarded_fields.md](viewsets/guarded_fields.md)) runs it after validation, because only then is the payload coerced and comparable to the stored row. It runs on every write path, including overridden handlers and custom actions. A write that slips past fails and is rolled back. Echoing a field back unchanged is not a change.
- **`setting={"names_issuer": True}`**: like `fields=`, but guards only a change *to* that value. Use it for fields that are dangerous in one direction only: turning it on is guarded, turning it off is left to anyone. A target is one value. For several, use `guarding.values(Status.APPROVED, Status.FEATURED)`. For everything but some, use `guarding.other_than(Visibility.PRIVATE)`. For anything a literal can't say, use `guarding.matching(lambda uses: uses > 100)`. A bare set isn't accepted as "any of these", since it can't be told apart from a field whose value really is a set. `fields=` refuses a dict and points to `setting=`.
- **`message=`**: overrides the default ("You may not change this field." / "You may not perform this action.").
- **Negation works.** The predicate is evaluated as one expression over request and object. Under DRF's own operators, `~is_owner(...)` negates the default `has_permission` of True and refuses every request up front.
- **No object** (`create`, `list`, non-detail actions): an object-only part of the predicate is *unknown*, combined with three-valued logic, and an unknown answer allows. `is_owner` has nothing to hold against a row that doesn't exist yet, while `IsSuperUser` is still asked.
- **Fails loud, never silent.** A guard must be its own `permission_classes` entry: composing one raises `TypeError`, or `ImproperlyConfigured` at class definition when a composition hides it. A `fields=` guard on a viewset without `GuardedFieldsMixin` raises `ImproperlyConfigured` on every request instead of permitting everything, since its `has_permission` would otherwise answer True. So does one naming a field that none of the viewset's serializers has. A field that this particular request can't write, because `?only=`/`?exclude=` dropped it or the action's serializer doesn't carry it, is skipped instead.
- **Bound to the endpoint, not the field.** A serializer shared by two viewsets is guarded only on the viewsets that declare the guard. So if two viewsets share a serializer and only one guards a field, defining the second raises `ImproperlyConfigured`. A viewset that leaves the field open on purpose lists it in `unguarded_fields`. See [viewsets/guarded_fields.md](viewsets/guarded_fields.md).

## Building blocks

What the factories above are made of, public for use on their own:

- **`guarding_values(*values)`, `guarding_other_than(*values)`, `guarding_matching(predicate)`**: the markers behind `guarding.values(...)`, `guarding.other_than(...)` and `guarding.matching(...)`, which are the usual spellings.
- **`GuardTarget`**: base class of `setting=` targets. Subclass it and implement `matches(incoming)`. The built-in ones are `EqualsTarget` (a plain value), `OneOfTarget` (`guarding.values`), `NotOneOfTarget` (`guarding.other_than`), `MatchingTarget` (`guarding.matching`) and `ANY_VALUE` (a flat `fields=` list).
- **`evaluate_permission(permission, request, view, obj=None)`**: evaluates a permission instance, compositions included, as one predicate. Returns True, False, or None for "unknown without an object". This is the evaluation `guarding` uses, and why `~` works there.
- **`descriptor_attribute_name(descriptor)`**: the attribute a model or class descriptor reads (`User.reports` -> `"reports"`), as `user_property`/`object_property` resolve it. Raises `TypeError` when there is no name to find.
- **`permission_name(permission)`**: a class-style name for any permission entry, compositions included (`~A | B` -> `NotAOrB`).
