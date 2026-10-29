"""
`ProtectTruncate`, the trigger refusing TRUNCATE on a table, and `allow_truncate()` and
`flushing_through_truncate_protection()`, which let one through - the second for a test's flush.

`pgtrigger.Protect` can't refuse a TRUNCATE: it forces a row-level or AFTER statement trigger, and a
TRUNCATE fires only statement-level ones, which can't carry its `referencing` clause. An append-only
table that a TRUNCATE empties isn't append-only, so a table protected against DELETE wants this beside
it - `isik.E015` holds a project's to it:

    class Meta:
        triggers = [
            pgtrigger.Protect(name="append_only", operation=pgtrigger.Update | pgtrigger.Delete),
            ProtectTruncate(name="protect_truncate"),
        ]
"""

from contextlib import contextmanager

import pgtrigger
from django.test import TransactionTestCase

from isik._internal.translation import gettext as _


class ProtectTruncate(pgtrigger.Trigger):
    """A BEFORE TRUNCATE statement trigger refusing every TRUNCATE of its table, `pgtrigger.ignore()` aside."""

    when = pgtrigger.Before
    operation = pgtrigger.Truncate
    level = pgtrigger.Statement

    def get_func(self, model):
        return "RAISE EXCEPTION 'pgtrigger: Cannot truncate %', TG_TABLE_NAME; RETURN NULL;"


def truncate_protection_problems(app_configs):
    """
    Each model of `app_configs` whose rows a `pgtrigger.Protect` keeps from being deleted, while nothing
    refuses a TRUNCATE of its table - which empties it all at once. A condition on the delete protection
    can't carry over: a TRUNCATE fires one statement trigger, with no row to read, so its twin refuses
    the whole table. The system check `isik.E015` reports them.
    """
    labels = {config.label for config in app_configs}
    registered = pgtrigger.registered()
    refusing_truncate = {model._meta.db_table for model, trigger in registered if isinstance(trigger, ProtectTruncate)}
    return [
        _(
            "%(model)s refuses deleting its rows with %(trigger)s, and nothing refuses a TRUNCATE, which empties "
            "its table at once - add ProtectTruncate beside it."
        )
        % {"model": model._meta.label, "trigger": trigger.name}
        for model, trigger in registered
        if model._meta.app_label in labels
        and not model._meta.swapped
        and isinstance(trigger, pgtrigger.Protect)
        and "DELETE" in str(trigger.operation)
        and model._meta.db_table not in refusing_truncate
    ]


def truncate_protections():
    """The URIs of every registered `ProtectTruncate`, as `pgtrigger.ignore()` takes them."""
    return [
        f"{model._meta.label}:{trigger.name}"
        for model, trigger in pgtrigger.registered()
        if isinstance(trigger, ProtectTruncate)
    ]


@contextmanager
def allow_truncate():
    """Lets a TRUNCATE through every `ProtectTruncate` while it's open."""
    with pgtrigger.ignore(*truncate_protections()):
        yield


@contextmanager
def flushing_through_truncate_protection():
    """
    While it's open, the flush a `TransactionTestCase` - and so a pytest-django `transaction=True` test -
    ends with goes through every `ProtectTruncate`: that flush empties every table with a TRUNCATE, and
    would otherwise be refused. Open it for the whole test session, in a `conftest.py`:

        @pytest.fixture(autouse=True, scope="session")
        def _flushable():
            with flushing_through_truncate_protection():
                yield

    or around `unittest`'s run of a suite of `TransactionTestCase`s.
    """
    flush = TransactionTestCase._fixture_teardown

    def _fixture_teardown(self):
        with allow_truncate():
            flush(self)

    TransactionTestCase._fixture_teardown = _fixture_teardown
    try:
        yield
    finally:
        TransactionTestCase._fixture_teardown = flush
