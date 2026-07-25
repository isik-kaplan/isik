import datetime
import uuid

from rest_framework import serializers


class FakeSerializer:
    """
    Builds a throwaway serializer *class* from a simple {field_name: type_or_field} schema - for
    documenting an ad hoc request/response shape (e.g. drf-spectacular's `responses=`) without
    hand-writing a dedicated serializer for something that's never actually instantiated on real
    data.

        Fake = FakeSerializer("Fake", {"detail": str, "code": int})
        Fake()  # or Fake(many=True) for a list shape

    A schema value is a plain type from `type_fields` (extend via `register_types`), a Field
    subclass (instantiated with no arguments), or a Field instance (used as-is). `read_only`/
    `required` apply to every type/subclass-derived field; `field_kwargs={"field": {...}}`
    overrides that per field.

    Reusing the same `name` across calls requires `reuse=True`, otherwise it raises - build the
    serializer once and reuse the resulting class instead of calling this again under the same
    name.
    """

    type_fields = {
        str: serializers.CharField,
        int: serializers.IntegerField,
        float: serializers.FloatField,
        bool: serializers.BooleanField,
        list: serializers.ListField,
        dict: serializers.DictField,
        uuid.UUID: serializers.UUIDField,
        datetime.date: serializers.DateField,
        datetime.datetime: serializers.DateTimeField,
        datetime.time: serializers.TimeField,
        datetime.timedelta: serializers.DurationField,
    }

    _used_names = set()

    def __new__(
        cls, name, schema, *, base=serializers.Serializer, read_only=None, required=None, field_kwargs=None, reuse=False
    ):
        if name in cls._used_names and not reuse:
            raise ValueError(
                f"{name!r} was already used to build a {cls.__name__} - pass reuse=True if this is "
                "intentional, or build it once and reuse the resulting class instead of calling "
                "this again under the same name."
            )
        cls._used_names.add(name)

        field_kwargs = field_kwargs or {}
        fields = {
            field_name: cls._build_field(
                value, read_only=read_only, required=required, extra_kwargs=field_kwargs.get(field_name, {})
            )
            for field_name, value in schema.items()
        }
        meta = type("Meta", (getattr(base, "Meta", object),), {"fields": list(fields)})
        return type(name, (base,), {**fields, "Meta": meta})

    @classmethod
    def _build_field(cls, value, *, read_only, required, extra_kwargs):
        if isinstance(value, serializers.Field):
            return value
        field_cls = cls.type_fields.get(value)
        if field_cls is None:
            if not (isinstance(value, type) and issubclass(value, serializers.Field)):
                raise TypeError(f"{value!r} isn't a registered type, Field subclass, or Field instance")
            field_cls = value
        kwargs = {}
        if read_only is not None:
            kwargs["read_only"] = read_only
        if required is not None:
            kwargs["required"] = required
        kwargs.update(extra_kwargs)
        return field_cls(**kwargs)

    @classmethod
    def register_types(cls, mapping):
        cls.type_fields = {**cls.type_fields, **mapping}


class FakeErrorSerializer:
    """
    Builds a matching "<Name>Error" FakeSerializer from a real serializer's field names, each
    turned into a list-of-error-strings field - matching DRF's own validation error shape - plus
    the always-present `non_field_errors`.

        WidgetErrorSerializer = FakeErrorSerializer(WidgetSerializer)

    Field names come from instantiating `serializer_cls` and reading its resolved `.fields` -
    works the same regardless of whether it declares `Meta.fields`, `Meta.exclude`, or is a plain
    `Serializer` with no `Meta` at all.

    Subject to the same `reuse=True` requirement as FakeSerializer - the derived name can collide
    even when `extra_fields` differs between calls.
    """

    def __new__(cls, serializer_cls, *, extra_fields=None, reuse=False):
        field_names = serializer_cls().fields.keys()

        def error_field():
            return serializers.ListField(child=serializers.CharField(), required=False, read_only=True)

        schema = {field_name: error_field() for field_name in field_names}
        schema["non_field_errors"] = error_field()
        schema.update(extra_fields or {})
        name = f"{serializer_cls.__name__.removesuffix('Serializer')}Error"
        return FakeSerializer(name, schema, reuse=reuse)
