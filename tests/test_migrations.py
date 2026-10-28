"""Every installed app's migrations match its models - isik's own above all, which a project can't write."""

import os
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
