"""
A pytest plugin, registered by isik's `pytest11` entry point:

- When any table refuses a TRUNCATE (`ProtectTruncate` - a tracked model's append-only event table, the
  notifications ledger), the flush a `transaction=True` test ends with goes through - see
  `flushing_through_truncate_protection()`.
- Every SMS sender `ISIK['DJANGO']['SMS']` configures sends to `isik.django.sms.outbox` for the run - the
  locmem sender, given its own options - as Django's test runner does with email, and the outbox is emptied
  before each test.

Without Django, or with neither, it does nothing.

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


def sms_configured():
    """Whether Django is set up with SMS senders configured."""
    try:
        from django.apps import apps
    except ImportError:
        return False
    if not apps.ready:
        return False
    from isik.django.conf import django_area

    return isinstance(django_area("SMS"), dict) and bool(django_area("SMS"))


@pytest.fixture(autouse=True, scope="session")
def _isik_sms_to_outbox():
    if not sms_configured():
        yield
        return
    from isik.django.conf import django_area, override_isik

    locmem = {
        alias: {**config, "BACKEND": "isik.django.sms.backends.locmem.SMSBackend"}
        if isinstance(config, dict)
        else config
        for alias, config in django_area("SMS").items()
    }
    with override_isik(DJANGO={"SMS": locmem}):
        yield


@pytest.fixture(autouse=True)
def _isik_sms_outbox(_isik_sms_to_outbox):
    if sms_configured():
        from isik.django.sms.backends.locmem import empty

        empty()
    yield
