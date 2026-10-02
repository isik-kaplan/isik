# base

## BaseSerializer

Everything isik adds to a serializer that isn't about a model, on `Serializer`: `MetaCombiningMixin`
(combining `Meta.relational_fields`), `RequestContextMixin` (`current_request()`/`current_user()`),
`ConditionalSerializerMixin` (`?include=`/`?only=`/`?exclude=`). For a response that isn't a row - a
computed summary, a credential shown once.

```python
class UsageSerializer(BaseSerializer):
    requests = serializers.IntegerField()
    period = serializers.CharField()
```

## BaseModelSerializer

`BaseSerializer` plus everything that is about a model, on `ModelSerializer`:
`ModelSerializerRegistryMixin`, `FieldGuardsOnSaveMixin`, `CreateOnlyFieldsMixin`,
`WriteOnlyFieldsMixin`, `FlattenedOneToOneMixin`.

```python
class WidgetSerializer(BaseModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "count"]
        create_only_fields = ["name"]
        relational_fields = {"owner": relational_serializer(OwnerSerializer)}
```

- Pick and compose the individual mixins directly instead (see their own docs) if a project doesn't want the whole stack - both bases are convenience defaults, not required entry points.
- `meta_fields_to_combine = ["relational_fields"]` and an empty `_Meta.relational_fields = {}` are set on `BaseSerializer` specifically so `Meta.relational_fields` always merges across the hierarchy rather than needing every subclass to redeclare it.
