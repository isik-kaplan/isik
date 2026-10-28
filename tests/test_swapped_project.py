"""The swapped project's suite (`tests_swapped/`), every notifications model the project's own - in a
process of its own, since Django swaps a model once, at setup."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


# mutmut runs the suite against its own rewritten copy of isik; this starts a separate run of the real one,
# which tells it nothing about a mutant.
@pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="runs the unmutated source in a process of its own")
def test_the_swapped_projects_suite_passes():
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests_swapped",
            "--ds=tests_swapped.settings",
            "-p",
            "no:cacheprovider",
            "--create-db",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stdout[-5000:] + run.stderr[-2000:]
