# conditional_serializer

Controls which fields show up based on `?include=`, `?only=`, and `?exclude=` query params - all three support dotted paths reaching into nested serializers that also use this mixin, and values can repeat (`?x=a&x=b`) or be comma-separated (`?x=a,b`).

## ConditionalSerializerMixin

```python
class PersonSerializer(ConditionalSerializerMixin, ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "first_name", "last_name"]
        relational_fields = {"manager": relational_serializer("myapp.serializers.PersonSerializer")}

# GET /people/1/                                      -> no "manager" key at all
# GET /people/1/?include=manager                        -> "manager" nested in
# GET /people/1/?include=manager&only=manager.first_name -> "manager": {"first_name": "..."}
# GET /people/1/?exclude=last_name                       -> "last_name" dropped
```

- `?include=x,y` is the only way a `relational_fields` entry appears at all - declare it as a name -> zero-argument factory (see `relational_serializer`). `?only=`/`?exclude=` reach into an already-included nested field with a dotted path (`only=manager.first_name`), but `manager` still needs `?include=manager` alongside to be there in the first place.
- `?exclude=` is applied last, so it always wins over a matching `?include=`/`?only=`.

## relational_serializer

Builds a `relational_fields` factory so you don't hand-write the `lambda:`. Accepts the serializer class directly, or a dotted import-path string for self-reference (a class can't name itself mid-body).

```python
relational_fields = {
    "manager": relational_serializer("myapp.serializers.PersonSerializer"),
    "reports": relational_serializer("myapp.serializers.PersonSerializer", many=True),
}
```

- Defaults to `read_only=True` - these are include-only fields.

## serializer_method_include

Lets an ad hoc serializer built inside a `SerializerMethodField` join dotted `?include=`/`?only=`/`?exclude=` paths, same as a `relational_fields` entry. Return the serializer itself, not `.data` - the decorator calls that for you.

```python
owner_detail = serializers.SerializerMethodField()

@serializer_method_include
def get_owner_detail(self, obj):
    return OwnerSerializer(obj.owner, context=self.context)
```

- Field name is derived from the method name via DRF's `get_<field_name>` convention - no other naming scheme is supported.
