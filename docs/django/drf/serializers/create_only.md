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
