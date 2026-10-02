"""
Keeps `isik.__version__` equal to `pyproject.toml`'s version - the `sync-version` pre-commit hook.

`pyproject.toml` is the one place a version is bumped. When `isik/__init__.py` says something else,
this rewrites it and exits 1, the way pre-commit's own fixers do: the commit stops, the fix is in the
working tree, and staging it is the person's call. `tests/test_version.py` fails CI for a commit that
skipped the hook.

    python scripts/sync_version.py
"""

import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "isik" / "__init__.py"
VERSION_LINE = re.compile(r'^__version__ = "[^"]*"$', re.MULTILINE)


def pyproject_version(pyproject=PYPROJECT):
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def sync(init=INIT, version=None):
    """Writes `version` into `init`'s `__version__` line, returning whether anything changed."""
    version = version or pyproject_version()
    text = init.read_text()
    if not VERSION_LINE.search(text):
        raise SystemExit(f'{init} has no `__version__ = "..."` line to keep in sync.')
    synced = VERSION_LINE.sub(f'__version__ = "{version}"', text)
    if synced == text:
        return False
    init.write_text(synced)
    return True


def main(init=INIT, pyproject=PYPROJECT):
    version = pyproject_version(pyproject)
    if sync(init, version):
        print(f"{init.name}: __version__ set to {version} from pyproject.toml - stage it and commit again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
