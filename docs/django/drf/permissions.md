# permissions

A grab-bag of DRF `BasePermission`s: a few ready-made classes, plus factories that build a permission class from a callable/property/action list rather than requiring a hand-written subclass for every simple check.

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

Builds an object-level permission allowing access only when `request.user` matches `obj.<owner_field>`. With `of=`, it compares against an attribute of the user instead (dotted paths allowed), for rows owned by a tenant rather than a person.

```python
permission_classes = [is_owner("owner")]
permission_classes = [is_owner("tenant", of="organization")]  # obj.tenant == request.user.organization
```

- Object-level only. DRF never consults object permissions for `list`/`create`, so used alone it allows the whole collection. Pair it with a request-level permission, or scope it with `guarding(..., actions=[...])`.
- It deliberately has no refusing `has_permission`: inside `guarding`, "no object yet" has to read as unknown, not as refused.

## prevent_actions

Builds a permission denying the given viewset actions (`view.action`), e.g. to block `destroy` on an otherwise-writable viewset.

```python
permission_classes = [prevent_actions("destroy", "create")]
```

## object_property

The `user_property` counterpart for the row being acted on: builds an object-level permission from a boolean property or attribute on the object. Mostly useful negated inside `guarding`, which is what makes `~` work on it.

```python
guarding(~object_property(attribute="is_app"), actions=["update", "partial_update", "destroy"])
```

## user_property

Builds a permission from a boolean property or plain attribute on the user model - pass exactly one of `property_`/`attribute`. If the resolved value has a `.reason` attribute, it becomes the permission's denial `.message`.

```python
user_property(property_=User.is_verified)
user_property(attribute="is_verified")
```

## guarding

Narrows a permission, including any `&`/`|`/`~` composition, to some actions or some fields. It wraps the predicate rather than being a factory parameter, so it works for plain classes like `IsSuperUser` and for composed predicates alike. Pass exactly one of `fields`/`actions`.

```python
permission_classes = [
    MayIssuePublicInvitations,
    guarding(is_owner("issued_by"), fields=["names_issuer"]),
    guarding(is_owner("created_by") | IsSuperUser, actions=["partial_update", "rotate_secret"]),
    guarding(~object_property(attribute="is_app"), actions=["partial_update"]),
]
```

- **`actions=`**: the predicate applies only when `view.action` is listed, and refuses with a 403. It runs where DRF runs permissions, so it needs nothing else.
- **`fields=`**: the predicate applies only to a write that changes a listed field, and refuses with a `ValidationError` keyed by that field, so a form can render it beside the control. `GuardedFieldsMixin` (part of `BaseModelViewSet`, see [viewsets/guarded_fields.md](viewsets/guarded_fields.md)) runs it after validation, because only then is the payload coerced and comparable to the stored row. Echoing a field back unchanged is not a change.
- **`fields={"names_issuer": True}`**: guards only writes that set the field to that value. Use it for fields that are dangerous in one direction only: turning it on is guarded, turning it off is left to anyone.
- **`message=`**: overrides the default ("You may not change this field." / "You may not perform this action.").
- **Negation works.** The predicate is evaluated as one expression over request and object. Under DRF's own operators, `~is_owner(...)` negates the default `has_permission` of True and refuses every request up front.
- **No object** (`create`, `list`, non-detail actions): an object-only part of the predicate is *unknown*, combined with three-valued logic, and an unknown answer allows. `is_owner` has nothing to hold against a row that doesn't exist yet, while `IsSuperUser` is still asked.
- **Fails loud, never silent.** A guard must be its own `permission_classes` entry: composing one raises `TypeError`, or `ImproperlyConfigured` at class definition when a composition hides it. A `fields=` guard on a viewset without `GuardedFieldsMixin` raises `ImproperlyConfigured` on every request instead of permitting everything, since its `has_permission` would otherwise answer True. So does one naming a field the serializer doesn't have.
- **Bound to the endpoint, not the field.** A serializer shared by two viewsets is guarded only on the viewsets that declare the guard.
