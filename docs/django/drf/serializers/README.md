# serializers

Serializer mixins composed together in `base.py`'s `BaseModelSerializer` - registry, create-only fields, Meta-combining, request context helpers, and conditional include/only/exclude.

- [base.md](base.md) - `BaseModelSerializer`, everything below composed onto `ModelSerializer`
- [registry.md](registry.md) - `ModelSerializerRegistryMixin`, model -> serializer lookup
- [conditional_serializer.md](conditional_serializer.md) - `?include=`/`?only=`/`?exclude=` field control
- [create_only.md](create_only.md) - `Meta.create_only_fields`, settable once then locked
- [meta_combining.md](meta_combining.md) - merges `Meta` list/dict attributes with a base `_Meta`
- [request_context.md](request_context.md) - `current_request()`/`current_user()` helpers
