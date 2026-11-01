"""The pytest plugin letting a test's flush through each TRUNCATE protection - only where there's one."""

import subprocess
import sys

from django.test import TransactionTestCase

from isik import pytest_plugin
from isik.django.apps.common.db import triggers


def run(fixture):
    """Runs the session fixture's body: the flush as it is while it's open, and after."""
    generator = fixture.__wrapped__()
    next(generator)
    during = TransactionTestCase._fixture_teardown
    next(generator, None)
    return during, TransactionTestCase._fixture_teardown


def test_with_a_protected_table_the_flush_goes_through_while_the_session_runs():
    flush = TransactionTestCase._fixture_teardown

    during, after = run(pytest_plugin._isik_flushable)

    assert during is not flush
    assert after is flush


def test_without_one_nothing_changes(monkeypatch):
    monkeypatch.setattr(pytest_plugin, "truncate_protected", lambda: False)
    flush = TransactionTestCase._fixture_teardown

    assert run(pytest_plugin._isik_flushable) == (flush, flush)


def test_this_project_has_one():
    assert pytest_plugin.truncate_protected() is True


def test_protected_is_asked_of_the_registered_triggers(monkeypatch):
    monkeypatch.setattr(triggers, "truncate_protections", lambda: [])

    assert pytest_plugin.truncate_protected() is False


def test_not_before_the_apps_are_ready(monkeypatch):
    from django.apps import apps

    monkeypatch.setattr(apps, "ready", False)

    assert pytest_plugin.truncate_protected() is False


def test_without_django_it_isnt(monkeypatch):
    monkeypatch.setitem(sys.modules, "django.apps", None)

    assert pytest_plugin.truncate_protected() is False


def test_it_is_registered_for_pytest():
    from importlib.metadata import entry_points

    assert [
        (entry.name, entry.value) for entry in entry_points(group="pytest11") if entry.value.startswith("isik")
    ] == [("isik", "isik.pytest_plugin")]


def test_it_is_loaded_where_django_isnt():
    # What pytest does in a project with isik[cli] alone: import the plugin, with no Django to import.
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['django'] = None; import isik.pytest_plugin as plugin; "
            "assert plugin.truncate_protected() is False",
        ],
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stderr
