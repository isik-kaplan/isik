# write_only

`WriteOnlyFieldsMixin` - fields listed in `Meta.write_only_fields` are settable but never appear in the serialized output.

```python
class SocialAppSerializer(WriteOnlyFieldsMixin, ModelSerializer):
    class Meta:
        model = SocialApp
        fields = ["id", "name", "secret"]
        write_only_fields = ["secret"]

# secret is accepted on create/update, never included in a GET response
```

- Works by overriding `get_extra_kwargs()`, same mechanism as `CreateOnlyFieldsMixin`.
- A field name can't appear in both `write_only_fields` and `create_only_fields` on the same class - that combination would force both `read_only=True` and `write_only=True` on an update request, which DRF's `Field` itself rejects. Raised as `ImproperlyConfigured` at class-definition time instead of an opaque assertion error on the first PATCH/PUT.
