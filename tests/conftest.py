import fcntl
import os
import tempfile
from pathlib import Path

import pytest
from django.contrib.admin.sites import AdminSite
from hypothesis import HealthCheck, settings


# mutmut tests each mutant in its own fork()ed child process (see its __main__.py's main mutation
# loop) - a hypothesis-decorated test ends up running under a "different executor" than the one
# that ran it during mutmut's baseline/stats-collection pass in the parent, which is exactly what
# Hypothesis's differing_executors check exists to flag as a potential correctness issue. Here it's
# an artifact of the mutation-testing harness's own process model, not the tests - selected only
# via --hypothesis-profile=mutation (see pytest_add_cli_args in [tool.mutmut]), never the default.
settings.register_profile("mutation", suppress_health_check=[HealthCheck.differing_executors])


# mutmut runs up to `max_children` mutants concurrently, each its own fork()ed child process, all
# inheriting the identical settings.DATABASES from the parent. Left alone, every database-touching
# mutant running concurrently would derive and race to set up the exact same test database -
# pytest-django already ships this fix for pytest-xdist workers (django_db_modify_db_settings
# suffixes the name by worker id), but mutmut isn't xdist, so that path never engages. Claim one of
# a small, stable pool of suffixed names via a file lock instead, so concurrent children land on
# distinct databases - each slot's database is still migrated once and reused via --reuse-db by
# whichever later mutant next lands on that slot, not recreated per mutant.
_MUTMUT_DB_LOCKS_DIR = Path(tempfile.gettempdir()) / "isik-mutmut-db-locks"


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    mutant_under_test = os.environ.get("MUTANT_UNDER_TEST")
    if not mutant_under_test:
        # Not a mutmut mutant child (either plain pytest, or mutmut's own single-shot baseline/
        # stats run in the parent process) - nothing concurrent to isolate from.
        yield
        return

    from django.conf import settings as django_settings

    _MUTMUT_DB_LOCKS_DIR.mkdir(exist_ok=True)
    pool_size = (os.cpu_count() or 4) * 2
    lock_file = None
    slot = None
    for candidate in range(pool_size):
        f = open(_MUTMUT_DB_LOCKS_DIR / f"slot-{candidate}.lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            continue
        lock_file, slot = f, candidate
        break

    if lock_file is None:
        # Pool exhausted - shouldn't happen at 2x cpu count, but fall back to the unsuffixed
        # default rather than hang; worst case reintroduces the race this exists to prevent.
        yield
        return

    for db_settings in django_settings.DATABASES.values():
        test_name = db_settings.get("TEST", {}).get("NAME")
        if not test_name:
            if db_settings["ENGINE"] == "django.db.backends.sqlite3":
                continue
            test_name = f"test_{db_settings['NAME']}"
        if test_name == ":memory:":
            continue
        db_settings.setdefault("TEST", {})
        db_settings["TEST"]["NAME"] = f"{test_name}_mutmut{slot}"

    try:
        yield
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


# _claimed_related_names is a process-global registry (isik.django.apps.common._model_makers),
# used by every X() maker to catch two attachments fighting over the same reverse-accessor name.
# mutmut runs each mutant in its own fork()ed child, which inherits whatever the parent's own
# single "clean tests" pass already claimed - a test building a fixed-name model under
# @isolate_apps (not uuid-suffixed) can then find its own related_name looks "already claimed" by
# that stale, inherited entry regardless of what the mutation under test actually did, silently
# masking the mutation instead of exercising it. Reset before every test so each one always starts
# from a genuinely empty registry, not whatever the parent process happened to leave behind.
@pytest.fixture(autouse=True)
def _reset_claimed_related_names():
    from isik.django.apps.common import _model_makers

    _model_makers._claimed_related_names.clear()


@pytest.fixture
def admin_site():
    return AdminSite()


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create_superuser(username="admin", email="admin@example.com", password="password")
