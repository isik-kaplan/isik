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
"""

import calendar
import datetime
import uuid

from django.core.exceptions import FieldDoesNotExist
from django.db import models, router
from django.db.models import F

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption
from isik.django.apps.common.db.models import DatabaseTimestampsModel, VersionedModel


# What a token can be: each of these has a text form that is a valid ETag between quotes.
TOKEN_FIELDS = (models.IntegerField, models.DateTimeField, models.UUIDField)


class UnprovenConcurrencyToken(
    Exemption,
    rule="isik.concurrency-token.unproven",
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
    one of isik's triggers or declared with `ConcurrencyToken`.
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
    problems = []
    if not field.concrete:
        problems.append(_("%(model)s's concurrency token %(field)r is not a column.") % {"model": label, "field": name})
    if field.null:
        problems.append(_("%(model)s's concurrency token %(field)r can be null.") % {"model": label, "field": name})
    if not isinstance(field, TOKEN_FIELDS):
        problems.append(
            _("%(model)s's concurrency token %(field)r must be an integer, a datetime or a UUID.")
            % {"model": label, "field": name}
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


def _token_text(value):
    if isinstance(value, datetime.datetime):
        # Microseconds since the epoch, in whole numbers: a timestamptz's full resolution, which a
        # rendered datetime (a project's DATETIME_FORMAT, a JavaScript Date) may not keep.
        return str(calendar.timegm(value.utctimetuple()) * 1_000_000 + value.microsecond)
    if isinstance(value, uuid.UUID):
        return value.hex
    return str(value)


def concurrency_attname(model):
    """The attribute `model`'s concurrency token is held in on an instance - its column's, for a `ForeignKey`."""
    return model._meta.get_field(concurrency_field_name(model)).attname


def _etag(value):
    return f'"{_token_text(value)}"'


def concurrency_etag(instance):
    """
    `instance`'s concurrency token as a strong ETag - `'"1760000000123456"'` - opaque to a client,
    which sends it back as it got it rather than building one.
    """
    return _etag(getattr(instance, concurrency_attname(type(instance))))


def current_concurrency_etag(model, pk):
    """
    The ETag of the row `pk` as the database holds it now - None if there is no such row. Read from
    the database `model`'s writes are routed to: a write still in its transaction is visible only
    there, and a replica reads are routed to may lag behind it.
    """
    rows = model._base_manager.db_manager(router.db_for_write(model))
    values = rows.filter(pk=pk).values_list(concurrency_attname(model), flat=True)
    return next((_etag(value) for value in values), None)


def assert_concurrency_field_changes(instance):
    """
    Proves on the database that `instance`'s concurrency token changes on every write - for a test:

        def test_the_invoice_revision_is_a_concurrency_token(invoice):
            assert_concurrency_field_changes(invoice)

    It saves `instance`, then twice writes the token back to the value it already holds, with
    `QuerySet.update()`, back to back - so the clock barely moves - and raises
    `AssertionError` naming the first write that left a token an earlier one had left.
    """
    model = type(instance)
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
