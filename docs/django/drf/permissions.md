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

Builds an object-level permission allowing access only when `request.user` matches `obj.<owner_field>`.

```python
permission_classes = [is_owner("owner")]
```

## prevent_actions

Builds a permission denying the given viewset actions (`view.action`), e.g. to block `destroy` on an otherwise-writable viewset.

```python
permission_classes = [prevent_actions("destroy", "create")]
```

## user_property

Builds a permission from a boolean property or plain attribute on the user model - pass exactly one of `property_`/`attribute`. If the resolved value has a `.reason` attribute, it becomes the permission's denial `.message`.

```python
user_property(property_=User.is_verified)
user_property(attribute="is_verified")
```
