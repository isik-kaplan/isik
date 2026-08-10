from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import transaction
from django.db.models.fields.reverse_related import OneToOneRel

from isik.django.drf.error_handling import django_to_drf_validation_error


def _validate_field_names(cls):
    own_meta = cls.__dict__.get("Meta")
    if own_meta is None:
        return None
    model = getattr(own_meta, "model", None)
    flattened = getattr(own_meta, "flattened_one_to_one_fields", None)
    if model is None or not flattened:
        return None
    for field_name in flattened:
        try:
            relation = model._meta.get_field(field_name)
        except FieldDoesNotExist:
            raise ImproperlyConfigured(f"{cls.__name__}: '{field_name}' isn't a field on {model.__name__}.") from None
        if not isinstance(relation, OneToOneRel):
            raise ImproperlyConfigured(
                f"{cls.__name__}: '{field_name}' isn't a reverse one-to-one relation on {model.__name__}."
            )
    return flattened


class _OneToOneWriteMixin:
    """
    Private write-through base for FlattenedOneToOneMixin - splits validated_data between "goes on
    the parent" and "goes on the flattened one-to-one relation(s)", and persists both. Split out
    from the field-flattening half since this doesn't need to know how the fields got here, only
    which top-level validated_data keys are reverse-O2O accessors.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        _validate_field_names(cls)

    @transaction.atomic
    def create(self, validated_data):
        # Nested atomic - load-bearing even though BaseModel.save() already wraps itself in one:
        # that only covers a single .save() call. Without this, a parent that saved successfully
        # wouldn't be rolled back if the related object then failed to save.
        nested = self._pop_flattened_one_to_one(validated_data)
        instance = super().create(validated_data)
        for field_name, data in nested.items():
            self._write_flattened_one_to_one(instance, field_name, data)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        nested = self._pop_flattened_one_to_one(validated_data)
        instance = super().update(instance, validated_data)
        for field_name, data in nested.items():
            self._write_flattened_one_to_one(instance, field_name, data)
        return instance

    def _pop_flattened_one_to_one(self, validated_data):
        flattened = getattr(self.Meta, "flattened_one_to_one_fields", None) or {}
        return {name: validated_data.pop(name) for name in flattened if name in validated_data}

    @django_to_drf_validation_error
    def _write_flattened_one_to_one(self, parent, field_name, data):
        # Bypasses the nested serializer's own is_valid() entirely (see the "known non-goal" on
        # FlattenedOneToOneMixin), so full_clean() - not DRF - is what actually enforces any
        # model-level validator/clean() on the related object. Translated here so that surfaces
        # as a normal DRF ValidationError (400) instead of an unhandled 500.
        related = getattr(parent, field_name, None)  # None on the reverse accessor's DoesNotExist
        if related is not None:
            for attr, value in data.items():
                setattr(related, attr, value)
            related.save()
            return
        relation = parent._meta.get_field(field_name)
        related_model = relation.related_model
        fk_name = relation.field.name
        related_model.objects.create(**data, **{fk_name: parent})


class FlattenedOneToOneMixin(_OneToOneWriteMixin):
    """
    Exposes `Meta.flattened_one_to_one_fields = {accessor: NestedSerializerClass}` as if the
    nested serializer's own fields were declared directly on this one, and writes them through to
    the related object - creating it if the parent doesn't have one yet, updating it in place
    otherwise. `accessor` is the reverse one-to-one field name on the model.

        class WidgetProfileSerializer(serializers.ModelSerializer):
            class Meta:
                model = WidgetProfile
                fields = ["bio"]

        class WidgetSerializer(FlattenedOneToOneMixin, BaseModelSerializer):
            class Meta:
                model = Widget
                fields = ["id", "name"]
                flattened_one_to_one_fields = {"profile": WidgetProfileSerializer}

        # GET returns {"id": ..., "name": ..., "bio": ...} - bio is None if no profile row exists
        # yet. Writing bio creates or updates the WidgetProfile row automatically.

    Known non-goal: only per-field validation on the nested serializer carries over (fields are
    harvested individually, not run through the nested serializer's own `is_valid()`) -
    `Meta.validators`/object-level `validate()` on the nested serializer class do not apply. A
    model-level validator/`clean()` on the related object still applies though, via `full_clean()`
    inside its own `save()` - any Django `ValidationError` raised there is translated into a DRF
    one (`django_to_drf_validation_error`), so it comes back as a normal 400 instead of a 500.

    Not combined into `MetaCombiningMixin`'s `meta_fields_to_combine` by default -
    `flattened_one_to_one_fields` is inherently per-model, unlike `relational_fields`. A project
    that wants it merged across a hierarchy for the same model can opt in itself. If it does, this
    mixin needs to sit to the left of `MetaCombiningMixin` in the bases list so its own
    `__init_subclass__` validation sees the already-merged dict.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        flattened = _validate_field_names(cls)
        if not flattened:
            return
        seen = {}
        for field_name, nested_cls in flattened.items():
            for name in nested_cls().fields:
                if name in seen:
                    raise ImproperlyConfigured(
                        f"{cls.__name__}: flattened field '{name}' is declared by both "
                        f"'{seen[name]}' and '{field_name}'."
                    )
                seen[name] = field_name

    def get_fields(self):
        fields = super().get_fields()
        flattened = getattr(self.Meta, "flattened_one_to_one_fields", None)
        if flattened:
            for field_name, nested_cls in flattened.items():
                for name, field in nested_cls().fields.items():
                    new_source = f"{field_name}.{field.source}"
                    # Reconstructed through the constructor, not mutated on a deep-copied instance
                    # - Field.__deepcopy__ always rebuilds from the field's own stashed _args/
                    # _kwargs, and ModelSerializer.get_fields() deep-copies _declared_fields on
                    # every request, so a post-hoc `field.source = ...` mutation would silently
                    # vanish the next time this serializer is instantiated.
                    fields[name] = field.__class__(*field._args, **{**field._kwargs, "source": new_source})
        return fields
