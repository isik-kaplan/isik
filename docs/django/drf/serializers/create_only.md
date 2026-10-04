# create_only

`CreateOnlyFieldsMixin` - fields listed in `Meta.create_only_fields` are settable at creation, then forced read-only (and not required) on every update after that, including partial updates.

```python
class WidgetSerializer(CreateOnlyFieldsMixin, ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "slug", "name"]
        create_only_fields = ["slug"]

# create: slug is writable
# update/partial_update: slug is read_only=True, required=False - client-supplied values are ignored
```

- Works by overriding `get_extra_kwargs()`, so other `extra_kwargs` already set on a create-only field (e.g. `help_text`) survive the forced read-only.

## Refusing a change instead of ignoring it

`Meta.create_only_changes` says what an update trying to change a create-only field gets:

- `"ignore"` (the default) - as above: read-only on update, so a value sent for it is dropped and
  the update answers as if it hadn't been. Right for a field a client echoes back unchanged.
- `"refuse"` - a value that differs from the stored one is a 400 naming the field (code
  `create_only`), which a caller trying to move it can act on. The same value sent back unchanged
  still passes, so one serializer serves both clients. The field stays writable on update - only no
  longer required - since refusing has to see the value.

```python
class Meta:
    model = Widget
    fields = ["id", "slug", "name"]
    create_only_fields = ["slug"]
    create_only_changes = "refuse"

# PATCH {"slug": "same-as-stored"} -> 200
# PATCH {"slug": "moved"}          -> 400 {"slug": ["This field can't be changed once it's set."]}
```

Anything else is `ImproperlyConfigured`. "Changed" means the same as for field guards: the
validated value differs from the stored one, rows compared for a many-to-many.
