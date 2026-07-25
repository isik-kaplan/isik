# lazy_relations

Relation fields that resolve their target(s) lazily on every access rather than at class-body/import time, to avoid circular imports between serializer modules that reference each other.

## LazyPrimaryKeyRelatedField

```python
reviewer = LazyPrimaryKeyRelatedField(queryset_func=lambda: User.objects.all())
```

- `queryset_func` is called fresh on every `get_queryset()`, not memoized.

## LazyGenericRelatedField

Serializes a `GenericForeignKey` by delegating to whichever registered serializer matches the target model. `serializers_func` is called on every access and must return `{Model: serializer_instance}`.

```python
resource = LazyGenericRelatedField(lambda: {Note: NoteSerializer(), Widget: WidgetSerializer()})
```

- Read: picks the first entry in `type(instance).mro()` found in the dict, so a registered superclass acts as a fallback for unregistered subclasses.
- Write: raw input carries no type tag, so every registered serializer's `to_internal_value` is tried - exactly one must succeed, or the field fails with `no_data_match` (zero matches) or `ambiguous_data` (more than one).
