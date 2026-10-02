"""`isik.__version__` is the version isik was built and installed as - hatchling reads it from there."""

from importlib.metadata import version

import isik


def test_the_installed_package_reports_the_version_it_was_built_as():
    # pyproject.toml declares the version dynamic, read from isik/__init__.py ([tool.hatch.version]) -
    # if that wiring broke, the installed metadata and the attribute would part ways here.
    assert version("isik") == isik.__version__
