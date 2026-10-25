"""
Concurrency tokens - the field a row's version is read from, so that a write made against an older
version can be told apart from one made against the current one, and refused (`PreconditionMixin`
in `isik.django.drf.viewsets.preconditions` sends it as an `ETag` and compares `If-Match` with it).

A model's token is its `concurrency_field`: by default `version` on a `VersionedModel`, else
`updated_at` on a `DatabaseTimestampsModel` - each kept by one of isik's own triggers, which change
it on every UPDATE whatever wrote it. Any other field is declared, with the reason it can be trusted:

    class Invoice(models.Model):
        revision = models.BigIntegerField()
        concurrency_field = ConcurrencyToken("revision", reason="bumped by the ledger's own trigger on every write")

A version can cover many-to-many relations too - a membership changing moves no column of the row,
so a token alone would let a write made before it through:

    class User(BaseModel):
        concurrency_relations = ("groups", "user_permissions")

The ETag is then computed in SQL from the token and each relation's rows (`concurrency_etag_expression()`),
and a trigger generated on each relation's table locks the owner row whenever one of its rows changes.
`updated_at` keeps meaning "this row's own fields changed".
"""

import calendar
import datetime
import hashlib
import json
import uuid

from django.contrib.postgres.expressions import ArraySubquery
from django.core.exceptions import FieldDoesNotExist
from django.db import models, router
from django.db.models import F, Func, OuterRef
from django.db.models.functions import MD5, Cast, Concat, JSONObject

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption
from isik.django.apps.common.db.models import DatabaseTimestampsModel, VersionedModel


# What a token can be: each of these has a text form that is a valid ETag between quotes.
TOKEN_FIELDS = (models.IntegerField, models.DateTimeField, models.UUIDField)
# Where with_concurrency_etags() puts the ETag of a model whose version covers relations.
ETAG_ANNOTATION = "isik_concurrency_etag"


class UnprovenConcurrencyToken(
    Exemption,
    rule="isik.concurrency-token.unproven",
    changes_behavior=True,
    why=(
        "A concurrency token must change on every write, or a write made against an older version "
        "passes for one made against the current one - and isik can prove that only of its own triggers."
    ),
):
    """Why a field isik doesn't keep is trusted as a concurrency token - `ConcurrencyToken`'s reason."""


class ConcurrencyToken:
    """
    Names a field isik doesn't keep itself as a model's concurrency token, and says why it changes on
    every write - a trigger of the project's own, say, whose SQL isik can't read for that:

        concurrency_field = ConcurrencyToken("revision", reason="bumped by the ledger's own trigger on every write")

    The reason is an `UnprovenConcurrencyToken`, held to its floor and listed by `manage.py exemptions`.
    Prove it on a real database with `assert_concurrency_field_changes()`.
    """

    def __init__(self, field, *, reason):
        if not isinstance(field, str):
            raise TypeError(_("ConcurrencyToken takes the field's name, not %(value)r.") % {"value": field})
        self.field = field
        self.reason = UnprovenConcurrencyToken(reason=reason)


class ConcurrencyParts(
    Exemption,
    rule="isik.concurrency-parts.unproven",
    changes_behavior=True,
    why=(
        "A version computed in Python covers only what its concurrency_parts() reads, and nothing proves "
        "that's everything a write could lose - nor locks what it reads, unless its relations are named."
    ),
):
    """
    `concurrency_strategy = ConcurrencyParts(reason=...)`: the model's version is its token and what its
    own `concurrency_parts()` returns - see the module docstring.
    """


# The strategies a version covering relations is kept by - see concurrency_strategy().
COMPUTED, COLUMN, PARTS = "computed", "column", "parts"


def concurrency_strategy(model):
    """
    How `model`'s version covers more than its token, or None when it doesn't: `"computed"` (SQL over
    its `concurrency_relations`, the default once any are named), `"column"` (their changes move the
    token itself), or `"parts"` (`ConcurrencyParts` - the model's own `concurrency_parts()`).
    """
    declared = getattr(model, "concurrency_strategy", None)
    if isinstance(declared, ConcurrencyParts):
        return PARTS
    if declared is not None:
        return declared
    return COMPUTED if concurrency_relations(model) else None


def concurrency_field_name(model):
    """The name of `model`'s concurrency token - see the module docstring - or None when it has none."""
    declared = getattr(model, "concurrency_field", None)
    if isinstance(declared, ConcurrencyToken):
        return declared.field
    if declared is not None:
        return declared
    if issubclass(model, VersionedModel):
        return "version"
    if issubclass(model, DatabaseTimestampsModel):
        return "updated_at"
    return None


def _kept_by_isik(model, name):
    return (issubclass(model, VersionedModel) and name == "version") or (
        issubclass(model, DatabaseTimestampsModel) and name == "updated_at"
    )


def concurrency_token_problems(model):
    """
    Why `model`'s concurrency token can't be trusted, one sentence each - `[]` when it can: it must
    exist, be a concrete column that can't be null, be an integer, datetime or UUID, and be kept by
    one of isik's triggers or declared with `ConcurrencyToken` - and each of its
    `concurrency_relations` must be a many-to-many field of its own.
    """
    label = model._meta.label
    name = concurrency_field_name(model)
    if name is None:
        return [
            _(
                "%(model)s has no concurrency token - make it a VersionedModel or a DatabaseTimestampsModel, "
                "or name one with concurrency_field = ConcurrencyToken(field, reason=...)."
            )
            % {"model": label}
        ]
    try:
        field = model._meta.get_field(name)
    except FieldDoesNotExist:
        return [
            _("%(model)s's concurrency token %(field)r is not one of its fields.") % {"model": label, "field": name}
        ]
    if not field.concrete:
        # Nothing else about it matters - there's no value in the row to compare.
        return [_("%(model)s's concurrency token %(field)r is not a column.") % {"model": label, "field": name}]
    problems = []
    if field.null:
        problems.append(_("%(model)s's concurrency token %(field)r can be null.") % {"model": label, "field": name})
    if not isinstance(field, TOKEN_FIELDS):
        problems.append(
            _("%(model)s's concurrency token %(field)r must be an integer, a datetime or a UUID.")
            % {"model": label, "field": name}
        )
    problems += _strategy_problems(model, label)
    for relation in concurrency_relations(model):
        if _relation(model, relation) is None:
            problems.append(
                _(
                    "%(model)s's concurrency relation %(relation)r is neither one of its many-to-many fields nor a "
                    "foreign key or one-to-one pointing at it."
                )
                % {"model": label, "relation": relation}
            )
    if not _kept_by_isik(model, name) and not isinstance(getattr(model, "concurrency_field", None), ConcurrencyToken):
        problems.append(
            _(
                "%(model)s's concurrency token %(field)r isn't kept by one of isik's triggers - declare it "
                "with ConcurrencyToken(%(field)r, reason=...), saying why it changes on every write."
            )
            % {"model": label, "field": name}
        )
    return problems


def _strategy_problems(model, label):
    strategy = concurrency_strategy(model)
    if strategy not in (None, COMPUTED, COLUMN, PARTS):
        return [
            _(
                "%(model)s's concurrency_strategy is %(value)r - it takes 'computed', 'column' or "
                "ConcurrencyParts(reason=...)."
            )
            % {"model": label, "value": strategy}
        ]
    if strategy in (COMPUTED, COLUMN) and not concurrency_relations(model):
        return [
            _("%(model)s's concurrency_strategy %(value)r covers relations, and it names no concurrency_relations.")
            % {"model": label, "value": strategy}
        ]
    if strategy == PARTS and not callable(getattr(model, "concurrency_parts", None)):
        return [
            _("%(model)s's concurrency_strategy is ConcurrencyParts, and it has no concurrency_parts() method.")
            % {"model": label}
        ]
    return []


def _token_text(value):
    if isinstance(value, datetime.datetime):
        # Microseconds since the epoch, in whole numbers: a timestamptz's full resolution, which a
        # rendered datetime (a project's DATETIME_FORMAT, a JavaScript Date) may not keep.
        return str(calendar.timegm(value.utctimetuple()) * 1_000_000 + value.microsecond)
    if isinstance(value, uuid.UUID):
        return value.hex
    return str(value)


def has_concurrency_token(instance):
    """Whether `instance`'s model has a concurrency token - see `concurrency_field_name()`."""
    return concurrency_field_name(instance.__class__) is not None


def concurrency_attname(model):
    """The attribute `model`'s concurrency token is held in on an instance - its column's, for a `ForeignKey`."""
    return model._meta.get_field(concurrency_field_name(model)).attname


def _etag(value):
    return f'"{_token_text(value)}"'


def concurrency_relations(model):
    """The many-to-many relations `model`'s version covers, by name - `concurrency_relations`, `()` by default."""
    return tuple(getattr(model, "concurrency_relations", ()))


def _relation(model, name):
    """
    The relation `name` names: a many-to-many field of `model`'s own, or the accessor of a foreign key or
    one-to-one pointing at it (`discount_set`, a related_name) - None for anything else.
    """
    many = next((field for field in model._meta.local_many_to_many if field.name == name), None)
    if many is not None:
        return many
    return next(
        (
            relation
            for relation in model._meta.related_objects
            if not relation.many_to_many and relation.get_accessor_name() == name
        ),
        None,
    )


def _relation_table(relation):
    """(the model whose table holds the relation's rows, its key naming the owner row)."""
    if relation.many_to_many:
        through = relation.remote_field.through
        return through, through._meta.get_field(relation.m2m_field_name())
    return relation.related_model, relation.field


def _token_expression(model):
    field = model._meta.get_field(concurrency_field_name(model))
    if isinstance(field, models.DateTimeField):
        # Whole microseconds, as _token_text() has a datetime - text the session's time zone and date
        # style can't change.
        return Func(F(field.attname), template="(EXTRACT(EPOCH FROM %(expressions)s) * 1000000)::bigint")
    return F(field.attname)


def _relation_rows(relation):
    table, owner_key = _relation_table(relation)
    # Every column of the row but its own key and the owner's: a through row's related object and a
    # custom through model's own fields, or every field of a row pointing at the owner. In their order,
    # so the same rows always read the same.
    columns = [field.attname for field in table._meta.concrete_fields if field not in (owner_key, table._meta.pk)]
    rows = table._base_manager.filter(**{owner_key.attname: OuterRef(owner_key.target_field.attname)})
    return ArraySubquery(rows.order_by(*columns).values(row=JSONObject(**{column: F(column) for column in columns})))


def concurrency_etag_expression(model):
    """
    The SQL computing the ETag of a `model` row whose version covers relations: an MD5 of its token and
    of each relation's rows. One expression wherever it's read - under the row lock, by
    `current_concurrency_etag()`, in `with_concurrency_etags()` - so they can't disagree. The same rows
    always give the same ETag: one removed and added back changes nothing a write could lose.
    """
    token = _token_expression(model)
    relations = [_relation_rows(_relation(model, name)) for name in concurrency_relations(model)]
    # Concat casts each part to text, and an array's text is braced, so the parts can't run into each
    # other. Concat needs two; a model covering no relation hashes its token alone.
    text = Concat(token, *relations, output_field=models.TextField()) if relations else Cast(token, models.TextField())
    return MD5(text)


def with_concurrency_etags(queryset):
    """
    `queryset` with each row's ETag read in the same query, for a list whose items show one - a model
    whose version is computed over relations needs a query of its own per row otherwise. Any other
    model's ETag is read from the row itself, or by its own concurrency_parts(), so `queryset` comes
    back as it is.
    """
    if concurrency_strategy(queryset.model) != COMPUTED:
        return queryset
    return queryset.annotate(**{ETAG_ANNOTATION: concurrency_etag_expression(queryset.model)})


def concurrency_etag(instance):
    """
    `instance`'s version as a strong ETag - `'"1760000000123456"'` - opaque to a client, which sends it
    back as it got it rather than building one. Computed over relations, it's what
    `with_concurrency_etags()` read with the row, or else what the database holds now; with
    `ConcurrencyParts`, the instance's token and what its `concurrency_parts()` returns now.
    """
    # __class__, not type(): a SimpleLazyObject (request.user) proxies the one, not the other.
    model = instance.__class__
    strategy = concurrency_strategy(model)
    if strategy == PARTS:
        return _parts_etag(instance)
    if strategy != COMPUTED:
        return _etag(getattr(instance, concurrency_attname(model)))
    annotated = getattr(instance, ETAG_ANNOTATION, None)
    return f'"{annotated}"' if annotated is not None else current_concurrency_etag(model, instance.pk)


def _parts_etag(instance):
    parts = [_token_text(getattr(instance, concurrency_attname(instance.__class__))), instance.concurrency_parts()]
    # default=str: a datetime, a UUID or a Decimal among the parts reads as its text.
    text = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    # SHA-256, which a FIPS build of Python allows, unlike MD5.
    return f'"{hashlib.sha256(text.encode()).hexdigest()}"'


def current_concurrency_etag(model, pk):
    """
    The ETag of the row `pk` as the database holds it now - None if there is no such row. Read from
    the database `model`'s writes are routed to: a write still in its transaction is visible only
    there, and a replica reads are routed to may lag behind it.
    """
    rows = model._base_manager.db_manager(router.db_for_write(model)).filter(pk=pk)
    strategy = concurrency_strategy(model)
    if strategy == PARTS:
        return next((_parts_etag(instance) for instance in rows), None)
    if strategy == COMPUTED:
        values = with_concurrency_etags(rows).values_list(ETAG_ANNOTATION, flat=True)
        return next((f'"{value}"' for value in values), None)
    values = rows.values_list(concurrency_attname(model), flat=True)
    return next((_etag(value) for value in values), None)


def assert_concurrency_field_changes(instance, related=None):
    """
    Proves on the database that `instance`'s version changes on every write - for a test:

        def test_the_invoice_revision_is_a_concurrency_token(invoice):
            assert_concurrency_field_changes(invoice)

    It saves `instance`, then twice writes the token back to the value it already holds, with
    `QuerySet.update()`, back to back - so the clock barely moves - and raises
    `AssertionError` naming the first write that left a token an earlier one had left.

    A model whose version covers relations takes one per relation in `related`. For a many-to-many it's
    an object - or `(obj, through_defaults)` for a through model with fields of its own - added, removed,
    added again and cleared. For a foreign key or one-to-one pointing at the model it's a function making
    one such row for the instance it's given, then deleted. Every write must move the ETag from the one
    before it.

        assert_concurrency_field_changes(user, related={"groups": group, "user_permissions": permission})
    """
    model = instance.__class__
    name = concurrency_field_name(model)
    if name is None:
        raise AssertionError(_("%(model)s has no concurrency token.") % {"model": model._meta.label})
    attname = concurrency_attname(model)
    row = model._base_manager.filter(pk=instance.pk)
    writes = [("save()", instance.save), *[("QuerySet.update()", lambda: row.update(**{attname: F(attname)}))] * 2]
    seen = [row.values_list(attname, flat=True).get()]
    for write, run in writes:
        run()
        token = row.values_list(attname, flat=True).get()
        if token in seen:
            raise AssertionError(
                _("%(model)s's concurrency token %(field)r was %(token)r again after %(write)s.")
                % {"model": model._meta.label, "field": name, "token": token, "write": write}
            )
        seen.append(token)
    related = related or {}
    for relation in concurrency_relations(model):
        if relation not in related:
            raise AssertionError(
                _("%(model)s's version covers %(relation)r - pass related={%(relation)r: <an object to add>}.")
                % {"model": model._meta.label, "relation": relation}
            )
        _assert_relation_moves_the_etag(instance, relation, related[relation])


def _many_to_many_writes(instance, relation, related):
    obj, through_defaults = related if isinstance(related, tuple) else (related, None)
    manager = getattr(instance, relation)
    return [
        ("add()", lambda: manager.add(obj, through_defaults=through_defaults)),
        ("remove()", lambda: manager.remove(obj)),
        ("add()", lambda: manager.add(obj, through_defaults=through_defaults)),
        ("clear()", manager.clear),
    ]


def _pointing_writes(instance, relation, make):
    if not callable(make):
        raise AssertionError(
            _(
                "%(model)s's version covers %(relation)r, rows pointing at it - pass related={%(relation)r: "
                "<a function making one for the instance it's given>}."
            )
            % {"model": instance.__class__._meta.label, "relation": relation}
        )
    made = []
    return [("create", lambda: made.append(make(instance))), ("delete()", lambda: made[0].delete())]


def _assert_relation_moves_the_etag(instance, relation, related):
    model = instance.__class__
    if _relation(model, relation).many_to_many:
        writes = _many_to_many_writes(instance, relation, related)
    else:
        writes = _pointing_writes(instance, relation, related)

    def read():
        return current_concurrency_etag(model, instance.pk)

    before = read()
    for write, run in writes:
        run()
        after = read()
        if after == before:
            raise AssertionError(
                _("%(model)s's ETag was %(etag)s still after %(relation)s.%(write)s.")
                % {"model": model._meta.label, "etag": after, "relation": relation, "write": write}
            )
        before = after
