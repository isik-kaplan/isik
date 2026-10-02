"""`isik.__version__` is pyproject.toml's version - and the hook that keeps it so does what it says."""

import os

import pytest


# mutmut runs the suite from its own copy of isik and tests, which has no scripts/ to import - and a
# check on the repository's files has nothing to say about a mutant's behaviour.
if "MUTANT_UNDER_TEST" in os.environ:
    pytest.skip("reads repository files, not code", allow_module_level=True)

import isik  # noqa: E402
from scripts import sync_version  # noqa: E402


def test_the_package_reports_the_version_it_was_released_as():
    assert isik.__version__ == sync_version.pyproject_version()


@pytest.fixture
def init(tmp_path):
    path = tmp_path / "__init__.py"
    path.write_text('"""isik."""\n\n__version__ = "0.1.0"\n')
    return path


def test_a_stale_version_is_rewritten_and_reported(init):
    assert sync_version.sync(init, "0.13.0") is True
    assert init.read_text() == '"""isik."""\n\n__version__ = "0.13.0"\n'


def test_a_current_version_is_left_alone(init):
    sync_version.sync(init, "0.13.0")

    assert sync_version.sync(init, "0.13.0") is False


def test_a_file_without_the_line_is_refused_rather_than_guessed_at(tmp_path):
    init = tmp_path / "__init__.py"
    init.write_text("VERSION = '1'\n")

    with pytest.raises(SystemExit, match="has no `__version__"):
        sync_version.sync(init, "0.13.0")


def test_the_hook_fails_the_commit_only_when_it_had_to_fix_something(init, tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.13.0"\n')

    assert sync_version.main(init, pyproject) == 1
    assert capsys.readouterr().out == (
        "__init__.py: __version__ set to 0.13.0 from pyproject.toml - stage it and commit again.\n"
    )
    assert sync_version.main(init, pyproject) == 0


def test_the_version_comes_from_the_project_table(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "isik"\nversion = "9.8.7"\n')

    assert sync_version.pyproject_version(pyproject) == "9.8.7"
