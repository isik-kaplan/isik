# guarded_fields

`GuardedFieldsMixin` runs the `fields=` half of [`guarding()`](../permissions.md#guarding). After a create or update validates, each field guard whose fields the write actually changes has its predicate evaluated against the stored row. A refusal raises `ValidationError({field: [message]})` with code `permission_denied`.

```python
class PublicInvitationViewSet(BaseModelViewSet):  # the mixin is already part of BaseModelViewSet
    permission_classes = [
        MayIssuePublicInvitations,
        guarding(is_owner("issued_by"), fields={"names_issuer": True}),
    ]
```

- "Changes" compares the typed, validated value to the stored one: a form-encoded `"false"` is compared as `False`, and a field sent back unchanged is not refused. Related managers compare their rows. A nested serializer's payload always counts as a change.
- On create there is no stored row, so every field present counts. An object-only predicate (e.g. `is_owner`) is unknown there and allows.
- Hooks `perform_create`/`perform_update`. A handler that saves a serializer without them has to call `self.check_guarded_fields(serializer)` itself, after `is_valid()`.
- Read-only fields are skipped, since they are never written. A guarded field name the serializer doesn't have raises `ImproperlyConfigured`.
- A guard nested inside `&`/`|`/`~` in `permission_classes` fails at class-definition time.
