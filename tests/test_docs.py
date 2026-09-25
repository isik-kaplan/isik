"""The docs' links work wherever they're read - GitHub's docs/, GitHub's repo root, and PyPI."""

import os
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
README = ROOT / "README.md"
REPOSITORY = "https://github.com/isik-kaplan/isik/blob/master/"
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")

# mutmut runs the suite from its own copy of the package and tests, which has no README or docs - and a
# check on the repository's files has nothing to say about a mutant's behaviour anyway.
pytestmark = pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="reads repository files, not code")


def links(path):
    return LINK.findall(path.read_text())


def is_absolute(target):
    return re.match(r"^[a-z]+:", target) is not None


@pytest.mark.parametrize("page", sorted(DOCS.rglob("*.md")), ids=lambda page: str(page.relative_to(ROOT)))
def test_every_relative_link_in_the_docs_resolves(page):
    broken = [target for target in links(page) if not is_absolute(target) and not (page.parent / target).exists()]
    assert broken == []


def test_the_readme_is_its_own_file():
    # A symlink to docs/INDEX.md would render that page's docs/-relative links from the repo root on
    # GitHub, and against pypi.org on PyPI - broken in both.
    assert not README.is_symlink()


def test_the_readme_links_absolutely_to_files_that_exist():
    # PyPI renders the README too, where a relative link resolves against pypi.org.
    targets = links(README)
    assert targets
    relative = [target for target in targets if not is_absolute(target)]
    assert relative == []
    missing = [
        target
        for target in targets
        if target.startswith(REPOSITORY) and not (ROOT / target.removeprefix(REPOSITORY)).exists()
    ]
    assert missing == []
