"""The admin is an extra of its own - `isik[django-admin]` - which `isik[django]` doesn't bring."""

import importlib
import sys
import tomllib
from pathlib import Path

import pytest


EXTRAS = tomllib.loads((Path(__file__).resolve().parents[5] / "pyproject.toml").read_text())["project"][
    "optional-dependencies"
]


def test_the_django_extra_brings_no_admin_packages():
    assert not {"dalf", "django-object-actions"} & set(EXTRAS["django"])
    assert EXTRAS["django-admin"] == ["isik[django]", "django-object-actions", "dalf"]


@pytest.mark.parametrize("missing", ["dalf", "django_object_actions"])
def test_importing_it_without_its_packages_names_the_extra(monkeypatch, missing):
    monkeypatch.setitem(sys.modules, missing, None)
    for name in [name for name in sys.modules if name.startswith("isik.django.apps.common.admin")]:
        monkeypatch.delitem(sys.modules, name)

    with pytest.raises(ImportError, match=r"pip install isik\[django-admin\]"):
        importlib.import_module("isik.django.apps.common.admin")
