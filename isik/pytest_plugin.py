"""
A pytest plugin, registered by isik's `pytest11` entry point: when the notifications ledger is installed,
the flush a `transaction=True` test ends with goes through the ledger's TRUNCATE protection - see
`flushing_through_truncate_protection()`. Without Django, or without the ledger, it does nothing.

It lives outside `isik.django`, whose import requires Django: pytest loads it in every project with isik
installed, one using only `isik[cli]` among them.
"""

import pytest


LEDGER_APP = "isik.django.apps.notifications"


def ledger_installed():
    """Whether Django is set up with the notifications ledger installed."""
    try:
        from django.apps import apps
    except ImportError:
        return False
    return apps.ready and apps.is_installed(LEDGER_APP)


@pytest.fixture(autouse=True, scope="session")
def _isik_notifications_flushable():
    # pytest-django has set Django up by the time a fixture runs, so the apps can be asked.
    if not ledger_installed():
        yield
        return
    from isik.django.apps.common.db.triggers import flushing_through_truncate_protection

    with flushing_through_truncate_protection():
        yield
