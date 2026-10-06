"""
What mutation testing here rests on: mutmut mutating decorated functions too (scripts/mutmut_decorators.py),
memoized functions starting every test with an empty cache, and nothing exempted from being mutated.

Each codegen case is a small source string put through mutmut's own generator and then run, because
what has to hold is what that generator emits and how it dispatches - not what the patch says it does.
"""

import ast
import importlib.util
import os
import sys
import tomllib
import types
from pathlib import Path

import libcst as cst
import pytest

from tests.conftest import memoized


ROOT = Path(__file__).resolve().parent.parent

# Checks on the tooling and the repository, not on isik's behavior - nothing for a mutant to change.
pytestmark = pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="checks the tooling, not code")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def patch(monkeypatch):
    """The patch, installed for the test and taken out after."""
    from mutmut.mutation import file_mutation

    module = _load("mutmut_decorators")
    monkeypatch.setattr(
        file_mutation.MutationVisitor, "_skip_node_and_children", file_mutation.MutationVisitor._skip_node_and_children
    )
    monkeypatch.setattr(file_mutation, "function_trampoline_arrangement", file_mutation.function_trampoline_arrangement)
    module.install()
    yield module
    module.uninstall()


def generate(source):
    from mutmut.__main__ import mutate_file_contents

    return mutate_file_contents("probe.py", source)


def functions(code):
    """{name: [decorator source, ...]} for every function the generated module defines."""
    found = {}
    module = cst.parse_module(code)

    class Collect(cst.CSTVisitor):
        def visit_FunctionDef(self, node):
            found[node.name.value] = [module.code_for_node(d.decorator) for d in node.decorators]

    module.visit(Collect())
    return found


def run(code, mutant, call, monkeypatch):
    """`call(module)` with `mutant` active, the generated code loaded as the module `probe`."""
    monkeypatch.setenv("MUTANT_UNDER_TEST", mutant)
    module = types.ModuleType("probe")
    monkeypatch.setitem(sys.modules, "probe", module)
    exec(compile(code, "probe.py", "exec"), module.__dict__)
    return call(module)


DECORATED = """\
import functools
from functools import wraps


def decorate(f):
    @wraps(f)
    def wrapper(*args):
        return f(*args) + 1

    return wrapper


@functools.lru_cache(maxsize=8)
def cached(x):
    return x + 1


class C:
    x = 10

    @property
    def p(self):
        return self.x + 1

    @classmethod
    @functools.cache
    def stacked(cls):
        return 1 + 2

    @classmethod
    def lone(cls):
        return 3 + 4
"""


class TestWhatTheGeneratorEmits:
    def test_stock_mutmut_skips_a_decorated_function(self):
        names = generate(DECORATED).mutant_names

        assert not [name for name in names if "cached" in name or "ǁp_" in name or "stacked" in name]

    def test_patched_every_decorated_function_is_mutated(self, patch):
        names = generate(DECORATED).mutant_names

        assert {name.rsplit("__mutmut_", 1)[0] for name in names} == {
            "x_decorate",
            "x_cached",
            "xǁCǁp",
            "xǁCǁstacked",
            "xǁCǁlone",
        }

    def test_the_trampoline_keeps_every_decorator_and_the_copies_only_what_they_need(self, patch):
        found = functions(generate(DECORATED).code)

        assert found["cached"] == ["functools.lru_cache(maxsize=8)", "_mutmut_mutated(mutants_x_cached__mutmut)"]
        assert found["x_cached__mutmut_orig"] == found["x_cached__mutmut_1"] == []
        assert found["p"] == ["property", "_mutmut_mutated(mutants_xǁCǁp__mutmut)"]
        assert found["xǁCǁp__mutmut_1"] == []
        assert found["stacked"] == ["classmethod", "functools.cache", "_mutmut_mutated(mutants_xǁCǁstacked__mutmut)"]
        assert found["xǁCǁstacked__mutmut_1"] == []
        assert found["lone"] == ["classmethod", "_mutmut_mutated(mutants_xǁCǁlone__mutmut, is_classmethod = True)"]
        assert found["xǁCǁlone__mutmut_1"] == ["classmethod"]

    def test_a_marking_decorator_stays_on_every_copy(self, patch):
        found = functions(generate("@makes_exemption(T)\ndef helper(reason):\n    return reason + 1\n").code)

        assert found["x_helper__mutmut_orig"] == found["x_helper__mutmut_1"] == ["makes_exemption(T)"]

    def test_a_decorators_own_arguments_are_never_mutated(self, patch):
        names = generate("@functools.lru_cache(maxsize=8)\ndef f(x):\n    return x\n").mutant_names

        assert names == []

    def test_install_twice_and_uninstall_put_stock_mutmut_back(self, patch):
        from mutmut.mutation import file_mutation

        patched = file_mutation.function_trampoline_arrangement
        patch.install()
        assert file_mutation.function_trampoline_arrangement is patched

        patch.uninstall()
        patch.uninstall()
        assert not [name for name in generate(DECORATED).mutant_names if "cached" in name]
        assert not hasattr(file_mutation, "_stock_mutmut")

    def test_a_lone_binding_decorator_and_a_marking_one_are_what_copies_carry(self, patch):
        def decorators(source):
            return [d.decorator for d in cst.parse_module(source).body[0].decorators]

        def carried(source):
            return [
                cst.Module([]).code_for_node(d.decorator)
                for d in patch.carried_by_copies([cst.Decorator(d) for d in decorators(source)])
            ]

        assert carried("@staticmethod\ndef f(): pass\n") == ["staticmethod"]
        assert carried("@classmethod\n@functools.cache\ndef f(): pass\n") == []
        assert carried("@x.makes_exemption(T)\n@wraps(g)\ndef f(): pass\n") == ["x.makes_exemption(T)"]
        assert carried("@(lambda f: f)\ndef f(): pass\n") == []


class TestEachMutantRunsThroughItsDecorators:
    @pytest.mark.parametrize(
        ("mutant", "call", "expected"),
        [
            (
                "",
                lambda m: (m.C().p, m.C.stacked(), m.C.lone(), m.cached(1), m.decorate(lambda: 1)()),
                (11, 3, 7, 2, 2),
            ),
            ("probe.xǁCǁp__mutmut_1", lambda m: m.C().p, 9),
            ("probe.xǁCǁstacked__mutmut_1", lambda m: m.C.stacked(), -1),
            ("probe.xǁCǁlone__mutmut_1", lambda m: m.C.lone(), -1),
            ("probe.x_cached__mutmut_2", lambda m: m.cached(1), 3),
            ("probe.x_decorate__mutmut_2", lambda m: m.decorate(lambda: 1)(), 3),
        ],
    )
    def test_the_active_mutant_is_what_runs(self, patch, monkeypatch, mutant, call, expected):
        assert run(generate(DECORATED).code, mutant, call, monkeypatch) == expected


def _memoized_in_source():
    """(module, qualified name) of every function isik's source decorates with a cache."""
    found = set()
    for path in sorted((ROOT / "isik").rglob("*.py")):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            body = getattr(node, "body", [])
            for child in body if isinstance(body, list) else []:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    names = {
                        ast.unparse(d.func if isinstance(d, ast.Call) else d).rsplit(".", 1)[-1]
                        for d in child.decorator_list
                    }
                    if names & {"cache", "lru_cache"}:
                        owner = node.name + "." if isinstance(node, ast.ClassDef) else ""
                        found.add((module, owner + child.name))
    return found


def test_every_memoized_function_has_its_cache_emptied_before_each_test():
    assert {(function.__module__, function.__qualname__) for function in memoized()} == _memoized_in_source()


def test_nothing_is_marked_never_to_be_mutated():
    marked = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in sorted((ROOT / "isik").rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if "pragma: no mutate" in line
    ]
    assert marked == []


def test_only_migrations_are_left_unmutated():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]

    assert config["do_not_mutate"] == ["*/migrations/*"]
