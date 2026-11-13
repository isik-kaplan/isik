"""
Rows kept inside a scope by the database: `ScopedModel`, the base of every model whose rows belong to one -
a campaign, a workspace - and `ScopedForeignKey`, a key from one such row to another in the same scope.

    class CampaignScoped(ScopedModel):
        scope_field = "campaign"
        campaign = models.ForeignKey("campaigns.Campaign", on_delete=models.PROTECT, related_name="+", ...)

        class Meta:
            abstract = True

    class Attempt(CampaignScoped): ...

    class Response(CampaignScoped):
        attempt = ScopedForeignKey("contact_history.Attempt", on_delete=models.PROTECT, related_name="responses")

The key is `(campaign_id, attempt_id)`, referencing `(campaign_id, id)` - a foreign key of two columns, so a
response's attempt is in the response's campaign, whatever writes the row. Each scoped model is unique on
`(scope, id)`, the columns a key to it references; each key has its own `<name>_id` column, and an index on
`(scope, <name>_id)` for the reverse lookup and the check a delete makes. Assigning `response.attempt = attempt`
sets the response's campaign from the attempt's too.
"""

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from django.db.backends.ddl_references import Columns, Statement, Table
from django.db.models.signals import class_prepared

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy, lazy_format
from isik.django.apps.common.db.constraints import fitted_name


class ScopedModel(models.Model):
    """
    A model whose rows each belong to one scope - `scope_field` names the field holding it, which a subclass
    declares. Each concrete subclass is unique on `(scope, id)`, so a `ScopedForeignKey` can reference it.
    """

    scope_field = None

    class Meta:
        abstract = True

    def clean_fields(self, exclude=None):
        # A key is read by its column; read by its name it's the row it keys to, fetched - and one in another scope
        # isn't there. Its constraint validates it instead, with the other constraints.
        return super().clean_fields(exclude={*(exclude or ()), *(key.name for key in scoped_keys(type(self)))})

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        # A key has no column, so Django before 6.0 keeps the row it last read - forget it with its columns.
        for key in scoped_keys(type(self)):
            if fields is None or {key.name, *key.from_fields} & set(fields):
                self._state.fields_cache.pop(key.cache_name, None)


def _set_null_column(collector, field, sub_objs, using):
    # SET_NULL, on the key's column - the key itself has none for the collector to update.
    collector.add_field_update(field.model._meta.get_field(field.column_name), None, sub_objs)


_set_null_column.lazy_sub_objs = True


class ScopedForeignKeyConstraint(models.BaseConstraint):
    """
    `FOREIGN KEY (fields) REFERENCES to (to_fields)`, deferrable where `deferrable` says - the database half of a
    `ScopedForeignKey`. `fields` and `to_fields` are field names; `to` is the referenced model's label.
    """

    def __init__(self, *, name, fields, to, to_fields, deferrable=None, **kwargs):
        super().__init__(name=name, **kwargs)
        self.fields = tuple(fields)
        self.to = to
        self.to_fields = tuple(to_fields)
        self.deferrable = deferrable

    def _target(self, model):
        return model._meta.apps.get_model(self.to)

    def create_sql(self, model, schema_editor):
        target = self._target(model)
        return Statement(
            schema_editor.sql_create_fk,
            table=Table(model._meta.db_table, schema_editor.quote_name),
            name=schema_editor.quote_name(self.name),
            column=Columns(
                model._meta.db_table,
                [model._meta.get_field(field).column for field in self.fields],
                schema_editor.quote_name,
            ),
            to_table=Table(target._meta.db_table, schema_editor.quote_name),
            to_column=Columns(
                target._meta.db_table,
                [target._meta.get_field(field).column for field in self.to_fields],
                schema_editor.quote_name,
            ),
            deferrable=schema_editor._deferrable_constraint_sql(self.deferrable),
        )

    def constraint_sql(self, model, schema_editor):
        # Added once every table of the migration exists, as Django adds its own foreign keys - the referenced
        # table may come later in it.
        schema_editor.deferred_sql.append(self.create_sql(model, schema_editor))
        return None

    def remove_sql(self, model, schema_editor):
        return schema_editor._delete_constraint_sql(schema_editor.sql_delete_fk, model, self.name)

    def validate(self, model, instance, exclude=None, using=None):
        if exclude and any(field in exclude for field in self.fields):
            return
        values = [getattr(instance, model._meta.get_field(field).attname) for field in self.fields]
        if any(value is None for value in values):
            return
        target = self._target(model)
        lookup = {target._meta.get_field(field).attname: values[index] for index, field in enumerate(self.to_fields)}
        if not target._default_manager.using(using).filter(**lookup).exists():
            raise ValidationError(self.get_violation_error_message(), code=self.violation_error_code)

    def __eq__(self, other):
        if not isinstance(other, ScopedForeignKeyConstraint):
            return NotImplemented
        return (self.name, self.fields, self.to, self.to_fields, self.deferrable) == (
            other.name,
            other.fields,
            other.to,
            other.to_fields,
            other.deferrable,
        ) and (self.violation_error_message, self.violation_error_code) == (
            other.violation_error_message,
            other.violation_error_code,
        )

    def __repr__(self):
        return f"<ScopedForeignKeyConstraint: {self.name} {self.fields} -> {self.to} {self.to_fields}>"

    def deconstruct(self):
        path, args, kwargs = super().deconstruct()
        kwargs.update(fields=self.fields, to=self.to, to_fields=self.to_fields)
        if self.deferrable is not None:
            kwargs["deferrable"] = self.deferrable
        return path, args, kwargs


class ScopedForeignKey(models.ForeignObject):
    """
    A key to a row of another `ScopedModel` in the same scope - `(scope, <name>_id)` referencing the target's
    `(scope, id)`, enforced by the database. Takes a `ForeignKey`'s arguments, and `deferrable`
    (`models.Deferrable`), when the database checks it: at each statement unless set. `scope` names the scope
    field; it's the model's `scope_field` unless given.

    `to_fields` names the target's fields the key references after the scope - its id unless given, any of its
    fields when given, the first held by `<name>_id`: `to_fields=("id", "kind")` references `(scope, id, kind)`,
    which a unique constraint of the target's holds. `from_fields` names the key's own field for each,
    `<name>_id` first - the same names as `to_fields` after it unless given.

    Its column is a field of its own, `<name>_id`, a `UUIDField` - every model isik keys has a UUID key - and the
    key sets it, and the scope, when assigned.
    """

    def __init__(self, to, on_delete, *, scope=None, deferrable=None, null=False, **kwargs):
        if on_delete is models.SET_NULL:
            on_delete = _set_null_column
        elif on_delete is models.SET_DEFAULT or getattr(on_delete, "deconstruct", None) is not None:
            raise TypeError(
                _("A ScopedForeignKey takes CASCADE, PROTECT, RESTRICT, SET_NULL or DO_NOTHING as its on_delete.")
            )
        to_fields = kwargs.pop("to_fields", None)
        from_fields = kwargs.pop("from_fields", None)
        if any(isinstance(given, str) or given == () for given in (to_fields, from_fields)):
            raise TypeError(_("A ScopedForeignKey's from_fields and to_fields are tuples of field names."))
        if from_fields is not None and (to_fields is None or len(from_fields) != len(to_fields)):
            raise TypeError(
                _("A ScopedForeignKey's from_fields name its own field for each of to_fields, in their order.")
            )
        # Made first, so a model inheriting the key from an abstract base copies the column before the key.
        # The comment is the column's: the key has no column of its own to say it on.
        self.column_field = models.UUIDField(
            null=null, blank=null, editable=False, db_comment=kwargs.pop("db_comment", None)
        )
        kwargs.setdefault("editable", False)
        super().__init__(to, on_delete, from_fields=[], to_fields=[], null=null, blank=null, **kwargs)
        self.scope = scope
        self.deferrable = deferrable
        self.key_to_fields = None if to_fields is None else tuple(to_fields)
        self.key_from_fields = None if from_fields is None else tuple(from_fields)

    @property
    def column_name(self):
        return f"{self.name}_id"

    @property
    def referenced(self):
        """The target's fields the key references after the scope - its `to_fields`, or its id."""
        return self.key_to_fields or ("id",)

    @property
    def referencing(self):
        """The key's own fields, one for each of `referenced` - its `from_fields`, or `<name>_id` and the same names."""
        return self.key_from_fields or (self.column_name, *self.referenced[1:])

    def contribute_to_class(self, cls, name):
        scope = self.scope or getattr(cls, "scope_field", None)
        if not scope:
            raise TypeError(
                _(
                    "%(model)s.%(field)s is a ScopedForeignKey on a model with no scope - base it on a "
                    "ScopedModel naming its scope_field, or give the key scope=."
                )
                % {"model": cls.__name__, "field": name}
            )
        self.scope = scope
        own = f"{name}_id"
        if self.key_from_fields and self.key_from_fields[0] != own:
            raise TypeError(
                _("%(model)s.%(field)s's from_fields start with its own column, %(column)s.")
                % {"model": cls.__name__, "field": name, "column": own}
            )
        if scope in (self.key_to_fields or ()) or scope in (self.key_from_fields or ()):
            raise TypeError(
                _(
                    "%(model)s.%(field)s leads its key with its scope, %(scope)s - from_fields and to_fields name "
                    "the rest."
                )
                % {"model": cls.__name__, "field": name, "scope": scope}
            )
        self.from_fields = [scope, own, *(self.key_from_fields or self.referenced)[1:]]
        self.to_fields = [scope, *(self.key_to_fields or (None,))]
        if not any(field.name == own for field in cls._meta.local_fields):
            column = self.column_field
            column.verbose_name = column._verbose_name = lazy_format(
                "%(name)s id", name=self.verbose_name or name.replace("_", " ")
            )
            column.help_text = self.help_text
            cls.add_to_class(own, column)
        super().contribute_to_class(cls, name)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        del kwargs["from_fields"], kwargs["to_fields"]
        if self.key_to_fields is not None:
            kwargs["to_fields"] = self.key_to_fields
        if self.key_from_fields is not None:
            kwargs["from_fields"] = self.key_from_fields
        kwargs.pop("blank", None)
        # Not editable is its default; editable is the one to say.
        if self.editable:
            kwargs["editable"] = True
        else:
            del kwargs["editable"]
        if kwargs["on_delete"] is _set_null_column:
            kwargs["on_delete"] = models.SET_NULL
        kwargs["scope"] = self.scope
        if self.deferrable is not None:
            kwargs["deferrable"] = self.deferrable
        return name, path, args, kwargs


def scoped_keys(model):
    """The `ScopedForeignKey`s `model` declares or inherits."""
    return [field for field in model._meta.local_fields if isinstance(field, ScopedForeignKey)]


def _label(model, target):
    """`target` - a model, `"app_label.Model"`, `"Model"` or `"self"` - as the label it names from `model`."""
    if not isinstance(target, str):
        return target._meta.label
    if target == "self":
        return model._meta.label
    return target if "." in target else f"{model._meta.app_label}.{target}"


def _hold_rows_in_scope(sender, **kwargs):
    # On every concrete scoped model, rather than in an abstract Meta: a subclass whose Meta doesn't inherit the
    # base's would drop them - and with them the scope.
    if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, ScopedModel) or not sender.scope_field:
        return
    table = sender._meta.db_table
    constraints = [
        models.UniqueConstraint(
            fields=[sender.scope_field, sender._meta.pk.name],
            name=fitted_name(table, "in_scope"),
            violation_error_message=gettext_lazy("A row's id is in its scope once."),
        )
    ]
    indexes = []
    for key in scoped_keys(sender):
        constraints.append(
            ScopedForeignKeyConstraint(
                name=fitted_name(table, f"{key.name}_in_scope"),
                fields=[key.scope, *key.referencing],
                to=_label(sender, key.remote_field.model),
                to_fields=[key.scope, *key.referenced],
                deferrable=key.deferrable,
                violation_error_message=lazy_format(
                    "The %(field)s is in another scope, or isn't there.", field=key.verbose_name
                ),
            )
        )
        indexes.append(
            models.Index(
                fields=[key.scope, *key.referencing],
                name=fitted_name(table, f"{key.name}_in_scope", limit=models.Index.max_name_length),
            )
        )
    # New lists: an inherited one is the parent Meta's own; and original_attrs is what a migration's state is
    # built from.
    sender._meta.constraints = [*sender._meta.constraints, *constraints]
    sender._meta.indexes = [*sender._meta.indexes, *indexes]
    sender._meta.original_attrs["constraints"] = sender._meta.constraints
    sender._meta.original_attrs["indexes"] = sender._meta.indexes


class_prepared.connect(_hold_rows_in_scope, dispatch_uid="isik_scoped_rows")


def _scope_problems(model):
    scope = model.scope_field
    if not scope:
        return [
            _("%(model)s is a ScopedModel naming no scope_field - name the field its scope is.")
            % {"model": model._meta.label}
        ]
    try:
        model._meta.get_field(scope)
    except FieldDoesNotExist:
        return [
            _("%(model)s's scope_field is %(scope)r, which isn't one of its fields.")
            % {"model": model._meta.label, "scope": scope}
        ]
    return []


def _key_problems(model, field):
    where = {"model": model._meta.label, "field": field.name}
    target = field.remote_field.model
    target_scope = getattr(target, "scope_field", None)
    if isinstance(field, ScopedForeignKey):
        if target_scope != field.scope:
            return [
                _(
                    "%(model)s.%(field)s keys to %(target)s, which isn't scoped by %(scope)s - scope it by the "
                    "same field."
                )
                % {**where, "target": target._meta.label, "scope": field.scope}
            ]
        if not isinstance(target._meta.pk, models.UUIDField):
            return [
                _("%(model)s.%(field)s keys to %(target)s, whose id isn't a UUID - key it by new_primary_key().")
                % {**where, "target": target._meta.label}
            ]
        return _target_problems(model, field, target)
    if field.name != model.scope_field and target_scope is not None and target_scope == model.scope_field:
        return [
            _(
                "%(model)s.%(field)s is a plain key between two models scoped by %(scope)s - declare it a "
                "ScopedForeignKey, so the database keeps both in one scope."
            )
            % {**where, "scope": model.scope_field}
        ]
    return []


def _target_problems(model, field, target):
    where = {"model": model._meta.label, "field": field.name, "target": target._meta.label}
    missing = [name for name in field.referencing[1:] if not _has_field(model, name)] + [
        name for name in field.referenced if not _has_field(target, name)
    ]
    if missing:
        return [
            _("%(model)s.%(field)s names %(names)s, which neither it nor %(target)s has.")
            % {**where, "names": ", ".join(missing)}
        ]
    if not isinstance(target._meta.get_field(field.referenced[0]), models.UUIDField):
        return [
            _("%(model)s.%(field)s holds %(target)s.%(name)s in its UUID column, and it isn't a UUID.")
            % {**where, "name": field.referenced[0]}
        ]
    columns = {field.scope, *field.referenced}
    unique = [set(fields) for fields in target._meta.unique_together] + [
        set(constraint.fields)
        for constraint in target._meta.total_unique_constraints
        if constraint.deferrable is None and constraint.fields
    ]
    if columns in unique:
        return []
    fields = [field.scope, *field.referenced]
    name = fitted_name(target._meta.db_table, "_".join([*field.referenced, "in_scope"]))
    return [
        _(
            "%(model)s.%(field)s references %(target)s's (%(columns)s), which no unique constraint of %(target)s "
            "holds - add models.UniqueConstraint(fields=%(fields)r, name=%(name)r) to its Meta.constraints."
        )
        % {**where, "columns": ", ".join(fields), "fields": fields, "name": name}
    ]


def _has_field(model, name):
    try:
        model._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return True


def scoped_key_problems(app_configs):
    """
    The problems with the scoped models of `app_configs`, one sentence each: a `ScopedModel` naming no scope field,
    a plain `ForeignKey` or `OneToOneField` between two models scoped by the same field, and a `ScopedForeignKey`
    to a model scoped by another field, keyed by something other than a UUID, naming a field that isn't there, or
    referencing columns no unique constraint of its target holds. The system check `isik.E025` reports them.
    """
    problems = []
    for config in app_configs:
        for model in config.get_models():
            if model._meta.proxy or not issubclass(model, ScopedModel):
                continue
            found = _scope_problems(model)
            if found:
                problems.extend(found)
                continue
            for field in model._meta.local_fields:
                if isinstance(field, models.ForeignObject) and not isinstance(field.remote_field.model, str):
                    problems.extend(_key_problems(model, field))
    return problems
