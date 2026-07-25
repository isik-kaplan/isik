# schema

Build throwaway serializer classes for documenting request/response shapes (e.g. drf-spectacular's `responses=`) without hand-writing a dedicated serializer for something that's never instantiated on real data.

## FakeSerializer

```python
from isik.django.drf.schema import FakeSerializer

Fake = FakeSerializer("Fake", {"detail": str, "code": int})
Fake()             # or Fake(many=True) for a list shape
```

- A schema value is a plain type from `type_fields` (str/int/float/bool/list/dict/UUID/date/datetime/time/timedelta - extend via `FakeSerializer.register_types`), a `Field` subclass (instantiated with no args), or a `Field` instance (used as-is, ignoring `read_only`/`required`/`field_kwargs`).
- Reusing the same `name` requires `reuse=True` - build the class once and reuse it instead of calling this again under the same name.

## FakeErrorSerializer

Builds a matching `"<Name>Error"` `FakeSerializer` from a real serializer's field names, each turned into a list-of-strings field matching DRF's validation error shape, plus `non_field_errors`.

```python
WidgetErrorSerializer = FakeErrorSerializer(WidgetSerializer)
```

- Works with any serializer shape - `Meta.fields`, `Meta.fields = "__all__"`, `Meta.exclude`, or a plain `Serializer` with no `Meta` at all - since field names come from instantiating the source serializer, not from reading `Meta` directly.
