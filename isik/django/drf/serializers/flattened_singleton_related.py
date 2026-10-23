"""FlattenedSingletonRelatedMixin - see its own docstring."""

from dataclasses import dataclass
from dataclasses import field as dataclass_field

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.db.models import Q, UniqueConstraint
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from isik._internal.translation import gettext as _
from isik.django.drf.error_handling import django_to_drf_validation_error


ON_EMPTY = ("delete", "clear", "refuse")

# Every serializer declaring one, for the system check - see singleton_related_problems().
_declaring = []


@dataclass(frozen=True)
class SingletonRelated:
    """
    One of a parent's related rows, by an equality `match`, flattened into the parent as one field:

        SingletonRelated("domains", {"is_primary": True}, "domain")

    `accessor` is the reverse foreign key the parent reaches the rows by, `match` picks the one -
    equality on the related model's own concrete fields, never the key back to the parent, which the
    manager supplies - and `attribute` is what of it the field shows and writes. `match` is also what
    a missing row is created with, so it can hold no lookup, callable or expression. `on_empty` says
    what an empty value (None or "") does: `"delete"` the row (the default - it exists only to carry
    this value), `"clear"` its attribute, or `"refuse"` the write.
    """

    accessor: str
    match: dict = dataclass_field(hash=False)
    attribute: str
    on_empty: str = "delete"

    def __post_init__(self):
        lookups = sorted(name for name in self.match if "__" in name)
        if lookups:
            raise ImproperlyConfigured(
                _(
                    "SingletonRelated(%(accessor)r)'s match names %(names)s - it takes equality on the related "
                    "model's own fields, which is also what a missing row is created with."
                )
                % {"accessor": self.accessor, "names": ", ".join(lookups)}
            )
        computed = sorted(
            name for name, value in self.match.items() if callable(value) or hasattr(value, "resolve_expression")
        )
        if computed:
            raise ImproperlyConfigured(
                _("SingletonRelated(%(accessor)r)'s match computes %(names)s - it takes plain values.")
                % {"accessor": self.accessor, "names": ", ".join(computed)}
            )
        if self.on_empty not in ON_EMPTY:
            raise ImproperlyConfigured(
                _("SingletonRelated(%(accessor)r)'s on_empty is %(value)r - it takes %(choices)s.")
                % {"accessor": self.accessor, "value": self.on_empty, "choices": ", ".join(ON_EMPTY)}
            )


def _relation(model, accessor):
    """The reverse foreign key `model` reaches its rows by through `accessor` - None for anything else."""
    return next(
        (
            relation
            for relation in model._meta.related_objects
            if relation.one_to_many and relation.get_accessor_name() == accessor
        ),
        None,
    )


class _SingletonField(serializers.Field):
    """The flattened field: reads the matching row's `attribute`, and writes it through `inner`'s validation."""

    def __init__(self, *, singleton, inner, **kwargs):
        self.singleton = singleton
        self.inner = inner
        super().__init__(source="*", required=False, **kwargs)

    def to_representation(self, parent):
        row = getattr(parent, self.singleton.accessor).filter(**self.singleton.match).first()
        if row is None:
            return None
        value = getattr(row, self.singleton.attribute)
        return None if value is None else self.inner.to_representation(value)

    def to_internal_value(self, data):
        if data in (None, ""):
            if self.singleton.on_empty == "refuse":
                raise ValidationError([_("This field may not be empty.")])
            return {self.field_name: None}
        return {self.field_name: self.inner.run_validation(data)}


class FlattenedSingletonRelatedMixin:
    """
    Exposes one row among a parent's many related ones as a field of the parent - a site's primary
    domain, say - read from it and written through to it:

        class SiteSerializer(FlattenedSingletonRelatedMixin, BaseModelSerializer):
            class Meta:
                model = Site
                fields = ["id", "name", "primary_domain"]
                flattened_singleton_related_fields = {
                    "primary_domain": SingletonRelated("domains", {"is_primary": True}, "domain"),
                }

    - Read: the matching row's attribute, or `None` when there's none - present and null. Listed in
      `Meta.fields` like any field (or published by `"__all__"`), and absent where it isn't: null says
      "no such row", absent says "this surface doesn't publish it".
    - Write, on create and update alike, in the save's transaction: a matching row is locked, set and
      saved with `update_fields=[attribute]` - the narrow save, writing nothing else of a row the
      serializer didn't load for that. No matching row: one is created from `match` and the value. A
      concurrent create loses on the unique constraint and is a 400, not a 500. An empty value does
      what `on_empty` says.
    - "The one" is proven, not assumed: the system check `isik.E006` refuses a declaration unless the
      related model has a partial `UniqueConstraint` over its key to the parent whose condition is
      exactly `match` - see `singleton_related_problems()`.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        own_meta = cls.__dict__.get("Meta")
        if getattr(own_meta, "flattened_singleton_related_fields", None):
            _declaring.append(cls)

    def _singletons(self):
        return getattr(self.Meta, "flattened_singleton_related_fields", None) or {}

    def get_field_names(self, declared_fields, info):
        # Its names aren't the model's, so ModelSerializer would refuse them - they're taken out here and
        # added in get_fields(), each only where Meta.fields lists it (or is "__all__").
        names = super().get_field_names(declared_fields, info)
        everything = getattr(self.Meta, "fields", None) == serializers.ALL_FIELDS
        self._published_singletons = [name for name in self._singletons() if everything or name in names]
        return [name for name in names if name not in self._singletons()]

    def get_fields(self):
        fields = super().get_fields()
        for name in self._published_singletons:
            singleton = self._singletons()[name]
            related = _relation(self.Meta.model, singleton.accessor).related_model
            model_field = related._meta.get_field(singleton.attribute)
            field_class, field_kwargs = self.build_standard_field(singleton.attribute, model_field)
            # Never handed an empty value - the field deals with that itself, by on_empty.
            inner = field_class(**field_kwargs)
            fields[name] = _SingletonField(singleton=singleton, inner=inner, allow_null=True)
        return fields

    @transaction.atomic
    def create(self, validated_data):
        values = self._pop_singletons(validated_data)
        instance = super().create(validated_data)
        self._write_singletons(instance, values)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        values = self._pop_singletons(validated_data)
        instance = super().update(instance, validated_data)
        self._write_singletons(instance, values)
        return instance

    def _pop_singletons(self, validated_data):
        return {name: validated_data.pop(name) for name in self._singletons() if name in validated_data}

    def _write_singletons(self, parent, values):
        for name, value in values.items():
            self._write_singleton(parent, name, self._singletons()[name], value)

    @django_to_drf_validation_error
    def _write_singleton(self, parent, name, singleton, value):
        rows = getattr(parent, singleton.accessor).filter(**singleton.match)
        if value is None and singleton.on_empty == "delete":
            rows.delete()
            return
        row = rows.select_for_update().first()
        if row is not None:
            setattr(row, singleton.attribute, value)
            row.save(update_fields=[singleton.attribute])
            return
        if value is None:
            return
        try:
            with transaction.atomic():
                getattr(parent, singleton.accessor).create(**singleton.match, **{singleton.attribute: value})
        except IntegrityError:
            raise ValidationError(
                {name: [_("This was set by another request at the same time - try again.")]}
            ) from None


def _matching_constraint(related, key, match):
    expected = set(match.items())
    for constraint in related._meta.constraints:
        if not isinstance(constraint, UniqueConstraint) or constraint.condition is None:
            continue
        condition = constraint.condition
        flat = (
            isinstance(condition, Q)
            and condition.connector == Q.AND
            and not condition.negated
            and all(isinstance(child, tuple) for child in condition.children)
        )
        if flat and set(condition.children) == expected and key.name in constraint.fields:
            return constraint
    return None


def singleton_related_problems():
    """
    Why each `FlattenedSingletonRelatedMixin` declaration can't be trusted, one sentence each - `[]`
    when they all can. `accessor` must be a reverse foreign key of the serializer's model; `match` and
    `attribute` concrete fields of the related model, not its key back; `on_empty="clear"` a nullable
    `attribute`; and a partial `UniqueConstraint` must cover the key back with exactly `match` as its
    condition. The system check `isik.E006` reports them.
    """
    problems = []
    for serializer in _declaring:
        model = serializer.Meta.model
        for name, singleton in serializer.Meta.flattened_singleton_related_fields.items():
            where = {"serializer": serializer.__name__, "name": name, "model": model._meta.label}
            relation = _relation(model, singleton.accessor)
            if relation is None:
                problems.append(
                    _("%(serializer)s.%(name)s: %(accessor)r isn't a reverse foreign key of %(model)s.")
                    % {**where, "accessor": singleton.accessor}
                )
                continue
            related, key = relation.related_model, relation.field
            concrete = {field.name: field for field in related._meta.concrete_fields if field is not key}
            for field_name in [*singleton.match, singleton.attribute]:
                if field_name not in concrete:
                    problems.append(
                        _(
                            "%(serializer)s.%(name)s: %(field)r isn't a concrete field of %(related)s other than its "
                            "key back to %(model)s."
                        )
                        % {**where, "field": field_name, "related": related._meta.label}
                    )
            attribute = concrete.get(singleton.attribute)
            if singleton.on_empty == "clear" and attribute is not None and not attribute.null:
                problems.append(
                    _("%(serializer)s.%(name)s: on_empty='clear' needs %(field)r to be nullable.")
                    % {**where, "field": singleton.attribute}
                )
            if _matching_constraint(related, key, singleton.match) is None:
                problems.append(
                    _(
                        "%(serializer)s.%(name)s: nothing proves %(related)s has one row per %(model)s matching "
                        "%(match)r - add UniqueConstraint(fields=[%(key)r], condition=Q(**%(match)r))."
                    )
                    % {**where, "related": related._meta.label, "match": singleton.match, "key": key.name}
                )
    return problems
