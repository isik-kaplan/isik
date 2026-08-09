# serializers

Serializer mixins composed together in `base.py`'s `BaseModelSerializer` - registry, create-only/write-only fields, Meta-combining, request context helpers, and conditional include/only/exclude.

- [base.md](base.md) - `BaseModelSerializer`, everything below composed onto `ModelSerializer`
- [registry.md](registry.md) - `ModelSerializerRegistryMixin`, model -> serializer lookup
- [conditional_serializer.md](conditional_serializer.md) - `?include=`/`?only=`/`?exclude=` field control
- [create_only.md](create_only.md) - `Meta.create_only_fields`, settable once then locked
- [write_only.md](write_only.md) - `Meta.write_only_fields`, settable but never serialized
- [flattened_one_to_one.md](flattened_one_to_one.md) - `Meta.flattened_one_to_one_fields`, a reverse O2O's fields exposed and written through as if they were the parent's own
- [history.md](history.md) - `generic_history_serializer`, a read-only serializer over a `@track_events()`-tracked model's history
- [meta_combining.md](meta_combining.md) - merges `Meta` list/dict attributes with a base `_Meta`
- [request_context.md](request_context.md) - `current_request()`/`current_user()` helpers
