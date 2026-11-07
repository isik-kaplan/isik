"""isik's exemption rules say where they live: `isik.django.` for the Django layer, `isik.` for the rest."""

import importlib
from pathlib import Path

import pytest

import isik
from isik.common.utils.exemptions import exemption_types
from isik.django.apps.common.exemptions import policy_rule


ROOT = Path(isik.__file__).parent


def _import_every_module_declaring_a_rule():
    # A type registers its rule when its module is imported - every one, not only what collection imported.
    for path in sorted(ROOT.rglob("*.py")):
        if "migrations" not in path.parts and "rule=" in path.read_text():
            importlib.import_module(".".join(("isik", *path.relative_to(ROOT).with_suffix("").parts)))


_import_every_module_declaring_a_rule()


def isiks_own():
    return {rule: kind for rule, kind in exemption_types().items() if kind.__module__.startswith("isik.")}


def test_there_are_rules_of_both_layers():
    modules = {kind.__module__ for kind in isiks_own().values()}

    assert any(module.startswith("isik.django.") for module in modules)
    assert any(not module.startswith("isik.django.") for module in modules)


@pytest.mark.parametrize("rule", sorted(isiks_own()))
def test_each_is_prefixed_by_its_layer(rule):
    module = isiks_own()[rule].__module__
    layer = "isik.django." if module.startswith("isik.django.") else "isik."

    assert rule.startswith(layer), f"{module} declares {rule!r} - isik's rules there start with {layer!r}."
    assert not (layer == "isik." and rule.startswith("isik.django.")), f"{module} isn't the Django layer."


def test_a_request_policys_rule_is_isiks_django_layers():
    class Reauthentication:
        pass

    assert policy_rule(Reauthentication) == "isik.django.policy.reauthentication"
