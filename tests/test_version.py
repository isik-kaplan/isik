"""`isik.__version__` is the version isik was built and installed as - hatchling reads it from there."""

import os
from importlib.metadata import version

import pytest

import isik


# A check on how the package is built and installed has nothing to say about a mutant's behavior.
pytestmark = pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="checks packaging, not code")


def test_the_installed_package_reports_the_version_it_was_built_as():
    # pyproject.toml declares the version dynamic, read from isik/__init__.py ([tool.hatch.version]) -
    # if that wiring broke, the installed metadata and the attribute would part ways here.
    assert version("isik") == isik.__version__
