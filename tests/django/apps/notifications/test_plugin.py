"""The pytest plugin letting a test's flush through the ledger's TRUNCATE protection - only where the ledger is."""

import subprocess
import sys

from django.test import TransactionTestCase

from isik import pytest_plugin


def run(fixture):
    """Runs the session fixture's body: the flush as it is while it's open, and after."""
    generator = fixture.__wrapped__()
    next(generator)
    during = TransactionTestCase._fixture_teardown
    next(generator, None)
    return during, TransactionTestCase._fixture_teardown


def test_with_the_ledger_the_flush_goes_through_while_the_session_runs():
    flush = TransactionTestCase._fixture_teardown

    during, after = run(pytest_plugin._isik_notifications_flushable)

    assert during is not flush
    assert after is flush


def test_without_it_nothing_changes(monkeypatch):
    monkeypatch.setattr(pytest_plugin, "ledger_installed", lambda: False)
    flush = TransactionTestCase._fixture_teardown

    assert run(pytest_plugin._isik_notifications_flushable) == (flush, flush)


def test_the_ledger_is_installed_here():
    assert pytest_plugin.ledger_installed() is True


def test_installed_is_asked_of_the_apps(monkeypatch):
    from django.apps import apps

    monkeypatch.setattr(apps, "is_installed", lambda name: name == "elsewhere")

    assert pytest_plugin.ledger_installed() is False


def test_without_django_it_isnt(monkeypatch):
    monkeypatch.setitem(sys.modules, "django.apps", None)

    assert pytest_plugin.ledger_installed() is False


def test_it_is_registered_for_pytest():
    from importlib.metadata import entry_points

    assert "isik.pytest_plugin" in [entry.value for entry in entry_points(group="pytest11")]


def test_it_is_loaded_where_django_isnt():
    # What pytest does in a project with isik[cli] alone: import the plugin, with no Django to import.
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['django'] = None; import isik.pytest_plugin as plugin; "
            "assert plugin.ledger_installed() is False",
        ],
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stderr
