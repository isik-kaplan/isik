# utils

Small standalone serializer field helpers - not composed into `BaseModelSerializer`, opt in individually per field.

- [current_user.md](current_user.md) - `CurrentUserField`, a client-can't-override current-user default
- [lazy_relations.md](lazy_relations.md) - `LazyPrimaryKeyRelatedField`/`LazyGenericRelatedField`, circular-import-safe relation fields
- [related_count.md](related_count.md) - `ModelRelatedCountField`, a related manager's `.count()` without fetching rows
