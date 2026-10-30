"""Every installed app's migrations match its models - isik's own above all, which a project can't write."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


# In a process of its own: a test here may leave a throwaway model registered, which makemigrations would
# then want a migration for. mutmut's rewritten copy of isik isn't what that process imports.
@pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="checks the unmutated source in a process of its own")
def test_no_app_needs_a_migration():
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "django",
            "makemigrations",
            "--check",
            "--dry-run",
            "--skip-checks",
            "--settings=tests.django_settings",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stdout[-5000:] + run.stderr[-2000:]


def test_each_of_isiks_migrations_waits_for_the_swappable_models_it_keys_to():
    # A project's app holding a swapped-in model migrates first, so no state a migration of isik's renders - a
    # trigger's does - names a model that isn't there yet. Django ignores the one naming its own app unswapped.
    # Read off the source: once loaded, a key to settings.X is only the model label it held.
    waiting = {}
    for path in sorted((ROOT / "isik").rglob("migrations/0*.py")):
        source = path.read_text()
        keyed = set(re.findall(r"to=settings\.(\w+)", source))
        awaited = set(re.findall(r"swappable_dependency\(settings\.(\w+)\)", source))
        if keyed - awaited:
            waiting[str(path.relative_to(ROOT))] = sorted(keyed - awaited)

    assert waiting == {}
