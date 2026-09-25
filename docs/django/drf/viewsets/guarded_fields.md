# guarded_fields

`GuardedFieldsMixin` runs the `fields=`/`setting=` guards of [`guarding()`](../permissions.md#guarding). After a create or update validates, each field guard whose fields the write actually changes has its predicate evaluated against the stored row. A refusal raises `ValidationError({field: [message]})` with code `permission_denied`.

```python
class PublicInvitationViewSet(BaseModelViewSet):  # the mixin is already part of BaseModelViewSet
    permission_classes = [
        MayIssuePublicInvitations,
        guarding(is_owner("issued_by"), setting={"names_issuer": True}),
    ]
```

- A guard answers for what the write **changes**, however the value arrives: from the payload, a serializer `default=`, or a `HiddenField` such as `CurrentUserField`. The model's own default is not a write.
- "Changes" compares the typed, validated value to the stored one: a form-encoded `"false"` is compared as `False`, and a field sent back unchanged is not refused. Related managers compare their rows. A nested serializer's payload, or a flattened one-to-one whose related row doesn't exist yet, always counts as a change.
- A partial update applies no defaults, so a PATCH leaves a `CurrentUserField` untouched. A full update reassigns it to the editor, and that is a change like any other.
- A `setting=` guard applies only when the incoming value matches its target (`guarding.values(...)`, `guarding.other_than(...)`, `guarding.matching(...)`).
- On create there is no stored row, so every value the write supplies counts. An object-only predicate (e.g. `is_owner`) is unknown there and allows.
- A `many=True` write is checked row by row, each as a new row.
- Fields this write can't reach are skipped: read-only fields, create-only fields on update, fields dropped by `?only=`/`?exclude=`, and fields the action's serializer doesn't carry. A guarded name that none of the viewset's serializers has (`serializer_class` and `serializer_class_action_map`) raises `ImproperlyConfigured`.
- **Shared serializers.** If two viewsets share a serializer and only one guards a field, defining the second raises `ImproperlyConfigured` and names both. A viewset that leaves the field open on purpose lists it in `unguarded_fields`:

  ```python
  class PlatformInvitationViewSet(BaseModelViewSet):
      serializer_class = PublicInvitationSerializer  # the tenant viewset guards names_issuer
      unguarded_fields = ["names_issuer"]
  ```

  Only viewsets with this mixin are compared, through their `permission_classes`, not a `get_permissions()` override. Redefining a class under the same module and name replaces its entry rather than conflicting.
- A guard nested inside `&`/`|`/`~` in `permission_classes` fails at class-definition time.

## No write path skips the guards

The check runs in three places, so overriding a handler can't bypass it.

1. **Before the write, inside `save()`**, for any serializer with [`FieldGuardsOnSaveMixin`](../serializers/guarded_save.md) (every `BaseModelSerializer`). This applies however the serializer was built, and counts values passed to `save(**kwargs)`.
2. **In `perform_create`/`perform_update`**, for a serializer without that mixin.
3. **After the fact, inside a transaction.** An unsafe request that succeeds without its guards having run raises `ImproperlyConfigured`, and its database writes roll back. That covers a plain DRF serializer saved by hand, or an ORM write in a custom action. The request runs in `transaction.atomic()` on every configured database; `field_guard_databases = ["default"]` narrows that, and `[]` turns it off.

An action that saves through no serializer and writes no guarded field says so:

```python
from isik.django.drf.viewsets import writes_no_guarded_fields

@writes_no_guarded_fields
@action(detail=True, methods=["post"])
def rotate_secret(self, request, pk=None):
    ...
```

- Either decorator order works. `destroy` writes no fields and is already exempt; `actions_writing_no_guarded_fields = ("destroy", "archive")` exempts inherited actions without overriding them.
- Calling `self.check_guarded_fields(serializer)` yourself also counts as having run the guards.
- A failed request (status 400 or above) isn't checked afterwards.
- **A refused request keeps nothing.** When DRF answers from an exception (a refused guard, a failed validation, any `APIException`), the transaction rolls back whatever the handler wrote before it, and `on_commit` callbacks registered along the way never run. An error response the handler returns on purpose (a 429 after counting an attempt) keeps its writes, as it does under `ATOMIC_REQUESTS`.
- Side effects outside the database (emails, HTTP calls, task queues without `transaction.on_commit`) aren't rolled back. That's what the loud failure is for: a test hitting the endpoint finds it.
- The transaction and the after-the-fact check apply to viewsets whose `permission_classes` declare a field guard. An action whose `get_permissions()` drops the field guards isn't checked.
