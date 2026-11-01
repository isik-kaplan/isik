"""
A pytest plugin, registered by isik's `pytest11` entry point: when any table refuses a TRUNCATE
(`ProtectTruncate` - a tracked model's append-only event table, the notifications ledger), the flush a
`transaction=True` test ends with goes through - see `flushing_through_truncate_protection()`. Without
Django, or with no such table, it does nothing.

It lives outside `isik.django`, whose import requires Django: pytest loads it in every project with isik
installed, one using only `isik[cli]` among them.
"""

import pytest


def truncate_protected():
    """Whether Django is set up with a table that refuses a TRUNCATE."""
    try:
        from django.apps import apps
    except ImportError:
        return False
    if not apps.ready:
        return False
    from isik.django.apps.common.db.triggers import truncate_protections

    return bool(truncate_protections())


@pytest.fixture(autouse=True, scope="session")
def _isik_flushable():
    # pytest-django has set Django up by the time a fixture runs, so the apps can be asked.
    if not truncate_protected():
        yield
        return
    from isik.django.apps.common.db.triggers import flushing_through_truncate_protection

    with flushing_through_truncate_protection():
        yield
