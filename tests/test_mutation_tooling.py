"""
What mutation testing here rests on: mutmut mutating decorated functions too (scripts/mutmut_decorators.py),
memoized functions starting every test with an empty cache, nothing exempted from being mutated, and a
kept `mutants/` reusing only what a change can't have made wrong (scripts/mutation_cache.py).

Each codegen case is a small source string put through mutmut's own generator and then run, because
what has to hold is what that generator emits and how it dispatches - not what the patch says it does.
"""

import ast
import importlib.util
import json
import os
import sys
import tomllib
import types
import zlib
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


SOURCES = {
    "pyproject.toml": '[tool.mutmut]\nsource_paths = ["isik"]\ndo_not_mutate = ["*/migrations/*"]\n',
    "uv.lock": "",
    "scripts/tool.py": "",
    "isik/__init__.py": "",
    "isik/a.py": "def f(): ...\ndef g(): ...\ndef k(): ...\n",
    "isik/b.py": "def h(): ...\n",
    "isik/sub/c.py": "def c(): ...\n",
    "isik/migrations/0001_initial.py": "",
    "isik/data.txt": "",
    "tests/conftest.py": "",
    "tests/test_a.py": "def test_f(): ...\ndef test_untraced(): ...\n",
    "tests/test_b.py": "def test_h(): ...\nclass TestH:\n    def test_h2(self): ...\n",
}
VERDICTS = {
    "isik/a.py": {
        "isik.a.x_f__mutmut_1": 1,
        "isik.a.x_f__mutmut_2": 0,
        "isik.a.x_g__mutmut_1": 33,
        "isik.a.x_k__mutmut_1": 3,
        "isik.a.x_k__mutmut_2": None,
    },
    "isik/b.py": {"isik.b.x_h__mutmut_1": 1, "isik.b.x_h__mutmut_2": 36},
    "isik/sub/c.py": {"isik.sub.c.x_c__mutmut_1": 1},
}
TRACED = {
    "isik.a.x_f": ["tests/test_a.py::test_f"],
    "isik.a.x_g": [],
    "isik.a.x_k": ["tests/test_b.py::test_h"],
    "isik.b.x_h": ["tests/test_b.py::test_h", "tests/test_b.py::TestH::test_h2"],
    "isik.sub.c.x_c": ["tests/test_a.py::test_f"],
}
# mutmut's own baseline and the rest - the cache has to hand these back as it found them.
MUTMUT_OWN = {"stats_time": 1.5, "function_hashes": {"isik.a.x_f": "1"}, "git_commit": "abc", "watched_file_hashes": {}}


@pytest.fixture
def cache():
    return _load("mutation_cache")


@pytest.fixture
def project(tmp_path, cache):
    """A checkout after a run: its sources, and a `mutants/` holding that run's verdicts and stats map."""
    for path, text in SOURCES.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    cache.refresh(tmp_path)
    mutants = tmp_path / "mutants"
    for path, text in SOURCES.items():
        if path.startswith(("isik/", "tests/")):
            (mutants / path).parent.mkdir(parents=True, exist_ok=True)
            (mutants / path).write_text(text)
    for path, verdicts in VERDICTS.items():
        meta = {"exit_code_by_key": verdicts, "hash_by_function_name": {}}
        (mutants / f"{path}.meta").write_text(json.dumps(meta))
        (mutants / f"{path}.spans").write_text("{}")
    durations = {test: 1.0 for tests in TRACED.values() for test in tests} | {"tests/test_a.py::test_untraced": 1.0}
    stats = {"tests_by_mangled_function_name": TRACED, "duration_by_test": durations, **MUTMUT_OWN}
    (mutants / "mutmut-stats.json").write_text(json.dumps(stats))
    (mutants / "mutation-kills.jsonl").write_text("{}\n")
    return tmp_path


def verdicts(project, path):
    return json.loads((project / "mutants" / f"{path}.meta").read_text())["exit_code_by_key"]


def stats(project):
    return json.loads((project / "mutants" / "mutmut-stats.json").read_text())


def stored_verdicts(project):
    return {path: verdicts(project, path) for path in VERDICTS}


class TestTheCacheKeepsOnlyWhatStillHolds:
    def test_with_no_earlier_run_it_starts_cold(self, tmp_path, cache):
        for path, text in SOURCES.items():
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(text)

        assert cache.refresh(tmp_path) == "full run: no earlier run to build on"
        assert [path.name for path in (tmp_path / "mutants").iterdir()] == [cache.BASELINE]

    def test_when_nothing_changed_every_verdict_stands_even_a_survivor(self, project, cache):
        assert cache.refresh(project) == "nothing changed: every stored verdict stands"
        assert stored_verdicts(project) == VERDICTS
        assert stats(project)["tests_by_mangled_function_name"] == TRACED

    def test_a_test_edited_in_place_loses_its_kills_and_its_trace(self, project, cache):
        (project / "tests/test_a.py").write_text("def test_f(): assert False\ndef test_untraced(): ...\n")

        cache.refresh(project)

        # f and c were killed through test_a's test_f, k and h through test_b's - which didn't change.
        assert verdicts(project, "isik/a.py") == {
            "isik.a.x_f__mutmut_1": None,
            "isik.a.x_f__mutmut_2": None,
            "isik.a.x_g__mutmut_1": None,
            "isik.a.x_k__mutmut_1": 3,
            "isik.a.x_k__mutmut_2": None,
        }
        assert verdicts(project, "isik/b.py") == {"isik.b.x_h__mutmut_1": 1, "isik.b.x_h__mutmut_2": None}
        assert verdicts(project, "isik/sub/c.py") == {"isik.sub.c.x_c__mutmut_1": None}
        assert stats(project)["tests_by_mangled_function_name"] == {
            "isik.a.x_f": [],
            "isik.a.x_g": [],
            "isik.a.x_k": ["tests/test_b.py::test_h"],
            "isik.b.x_h": ["tests/test_b.py::TestH::test_h2", "tests/test_b.py::test_h"],
            "isik.sub.c.x_c": [],
        }
        assert set(stats(project)["duration_by_test"]) == {"tests/test_b.py::test_h", "tests/test_b.py::TestH::test_h2"}

    def test_the_stats_map_keeps_what_mutmut_stores_beside_the_traces(self, project, cache):
        (project / "tests/test_a.py").write_text("")

        cache.refresh(project)

        assert {key: stats(project)[key] for key in MUTMUT_OWN} == MUTMUT_OWN

    def test_a_deleted_test_leaves_no_copy_to_run(self, project, cache):
        (project / "tests/test_b.py").unlink()

        cache.refresh(project)

        assert not (project / "mutants/tests/test_b.py").exists()
        assert (project / "mutants/tests/test_a.py").exists()
        assert set(stats(project)["duration_by_test"]) == {"tests/test_a.py::test_f", "tests/test_a.py::test_untraced"}

    def test_a_changed_module_makes_every_test_that_reached_it_stale(self, project, cache):
        (project / "isik/b.py").write_text("def h(): return 1\n")

        cache.refresh(project)

        # k is unchanged, in another module, but its kill came through test_h - which reaches h.
        assert verdicts(project, "isik/a.py")["isik.a.x_k__mutmut_1"] is None
        assert verdicts(project, "isik/a.py")["isik.a.x_f__mutmut_1"] == 1
        assert verdicts(project, "isik/b.py") == {"isik.b.x_h__mutmut_1": None, "isik.b.x_h__mutmut_2": None}
        assert stats(project)["tests_by_mangled_function_name"]["isik.a.x_k"] == []
        assert set(stats(project)["duration_by_test"]) == {"tests/test_a.py::test_f", "tests/test_a.py::test_untraced"}

    def test_a_changed_module_is_regenerated_whatever_the_file_times_say(self, project, cache):
        (project / "isik/b.py").write_text("def h(): return 1\n")

        cache.refresh(project)

        assert not (project / "mutants/isik/b.py").exists()
        assert (project / "mutants/isik/b.py.meta").exists()
        assert (project / "mutants/isik/a.py").exists()

    def test_a_deleted_module_leaves_nothing_behind(self, project, cache):
        (project / "isik/sub/c.py").unlink()

        cache.refresh(project)

        assert not (project / "mutants/isik/sub").exists()
        assert (project / "mutants/isik").is_dir()

    def test_a_new_test_file_only_resets_what_isnt_a_kill(self, project, cache):
        (project / "tests/test_new.py").write_text("def test_new(): ...\n")
        untouched = (project / "mutants/isik/sub/c.py.meta").read_text()

        cache.refresh(project)

        # c holds only a kill, so its data file is left as it was.
        assert (project / "mutants/isik/sub/c.py.meta").read_text() == untouched

        assert verdicts(project, "isik/a.py")["isik.a.x_f__mutmut_1"] == 1
        assert verdicts(project, "isik/a.py")["isik.a.x_g__mutmut_1"] is None
        assert stats(project)["tests_by_mangled_function_name"] == {key: sorted(tests) for key, tests in TRACED.items()}

    @pytest.mark.parametrize(
        "path",
        [
            "tests/conftest.py",
            "tests/helpers.py",
            "isik/migrations/0001_initial.py",
            "isik/data.txt",
            "pyproject.toml",
            "uv.lock",
            "scripts/tool.py",
        ],
    )
    def test_a_change_any_test_can_depend_on_starts_cold(self, project, cache, path):
        with (project / path).open("a") as file:
            file.write("# changed\n")

        assert cache.refresh(project) == f"full run: {path} changed, which any test can depend on"
        assert [path.name for path in (project / "mutants").iterdir()] == [cache.BASELINE]

    def test_a_full_run_can_be_asked_for(self, project, cache):
        assert cache.refresh(project, full=True) == "full run: asked for"
        assert [path.name for path in (project / "mutants").iterdir()] == [cache.BASELINE]

    @pytest.mark.parametrize("name", ["mutmut-stats.json", "mutation-baseline.json"])
    @pytest.mark.parametrize("damage", ["missing", "corrupt"])
    def test_without_a_stats_map_or_a_baseline_it_starts_cold(self, project, cache, name, damage):
        target = project / "mutants" / name
        if damage == "missing":
            target.unlink()
        else:
            target.write_text("{")

        assert cache.refresh(project) == "full run: no earlier run to build on"

    def test_files_mutmut_and_the_suite_keep_in_mutants_survive(self, project, cache):
        (project / "isik/b.py").write_text("def h(): return 1\n")

        cache.refresh(project)

        assert (project / "mutants/mutation-kills.jsonl").read_text() == "{}\n"

    def test_the_baseline_is_restamped_so_a_change_is_paid_for_once(self, project, cache):
        (project / "isik/b.py").write_text("def h(): return 1\n")
        cache.refresh(project)

        assert cache.refresh(project) == "nothing changed: every stored verdict stands"

    def test_bytecode_is_not_a_change(self, project, cache):
        (project / "isik/__pycache__").mkdir()
        (project / "isik/__pycache__/a.cpython-312.pyc").write_bytes(b"\0")

        assert cache.refresh(project) == "nothing changed: every stored verdict stands"

    def test_a_module_is_named_as_mutmut_keys_its_functions(self, cache):
        from mutmut.utils.format_utils import get_module_from_key, get_mutant_name

        for path in ("isik/a.py", "isik/sub/__init__.py"):
            assert cache.module_of(path) == get_module_from_key(get_mutant_name(Path(path), "x_f"))


def test_mutation_run_refreshes_the_cache_and_passes_everything_but_full_to_mutmut(monkeypatch):
    from mutmut import __main__ as mutmut_main

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        run = _load("mutation_run")
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    calls = []
    monkeypatch.setattr(run.mutmut_decorators, "install", lambda: calls.append("install"))
    monkeypatch.setattr(run.mutation_cache, "refresh", lambda full: calls.append(("refresh", full)) or "kept")
    monkeypatch.setattr(mutmut_main, "cli", lambda args, standalone_mode: calls.append(args))

    assert run.main(["--full", "isik.common.*"]) == 0
    assert calls == ["install", ("refresh", True), ["run", "isik.common.*"]]


class TestMutationShards:
    @pytest.fixture
    def shards(self, monkeypatch):
        import mutmut.__main__ as mutmut_main

        monkeypatch.setattr(
            mutmut_main, "collect_source_file_mutation_data", mutmut_main.collect_source_file_mutation_data
        )
        module = _load("mutation_shards")
        yield module
        module.uninstall()

    @pytest.mark.parametrize(("given", "parsed"), [("1/6", (1, 6)), ("6/6", (6, 6)), ("1/1", (1, 1))])
    def test_a_shard_is_index_over_count(self, shards, given, parsed):
        assert shards.parse(given) == parsed

    @pytest.mark.parametrize("given", ["0/6", "7/6", "2", "a/6", "2/b", ""])
    def test_anything_else_is_refused(self, shards, given):
        with pytest.raises(SystemExit):
            shards.parse(given)

    def test_every_mutant_in_exactly_one_shard_and_the_same_one_each_time(self, shards):
        names = [f"isik.module.x_function_{n}__mutmut_{k}" for n in range(200) for k in range(10)]

        placed = [shards.shard_of(name, 6) for name in names]

        assert set(placed) == {1, 2, 3, 4, 5, 6}
        assert placed == [shards.shard_of(name, 6) for name in names]
        # The name alone decides: crc32, which no interpreter's hash seed moves.
        assert shards.shard_of("isik.common.x_f__mutmut_1", 6) == zlib.crc32(b"isik.common.x_f__mutmut_1") % 6 + 1
        # About a sixth each: none over a fifth of the run.
        assert max(placed.count(index) for index in range(1, 7)) < len(names) / 5

    def test_installed_a_run_collects_its_shard_alone(self, shards, monkeypatch):
        import mutmut.__main__ as mutmut_main

        names = [f"isik.module.x_f__mutmut_{k}" for k in range(60)]
        asked = []

        def collect(*, mutant_names):
            asked.append(mutant_names)
            return [(None, name, None) for name in names], {"by": "path"}

        monkeypatch.setattr(mutmut_main, "collect_source_file_mutation_data", collect)
        shards.install(2, 3)
        shards.install(2, 3)

        mutants, by_path = mutmut_main.collect_source_file_mutation_data(mutant_names=("isik.*",))

        assert [name for _, name, _ in mutants] == [name for name in names if shards.shard_of(name, 3) == 2]
        assert by_path == {"by": "path"}
        assert asked == [("isik.*",)]
        shards.uninstall()
        shards.uninstall()
        assert mutmut_main.collect_source_file_mutation_data is collect

    def test_installed_the_shards_are_disjoint_and_the_whole(self, shards, monkeypatch):
        import mutmut.__main__ as mutmut_main

        names = [f"isik.module{n % 7}.x_function_{n}__mutmut_{k}" for n in range(300) for k in range(5)]
        monkeypatch.setattr(
            mutmut_main,
            "collect_source_file_mutation_data",
            lambda *, mutant_names: ([(None, name, None) for name in names], {}),
        )
        stock = mutmut_main.collect_source_file_mutation_data
        shares = []
        for index in range(1, 7):
            shards.install(index, 6)
            shares.append({name for _, name, _ in mutmut_main.collect_source_file_mutation_data(mutant_names=())[0]})
            shards.uninstall()

        assert mutmut_main.collect_source_file_mutation_data is stock
        assert sum(len(share) for share in shares) == len(names)
        assert set().union(*shares) == set(names)
        assert all(share for share in shares)

    def test_what_it_patches_is_still_mutmuts_to_patch(self):
        # A mutmut that renamed or reshaped this would leave a shard running every mutant, or none - loudly here.
        import inspect

        import mutmut.__main__ as mutmut_main

        parameters = inspect.signature(mutmut_main.collect_source_file_mutation_data).parameters
        assert [(name, parameter.kind) for name, parameter in parameters.items()] == [
            ("mutant_names", inspect.Parameter.KEYWORD_ONLY)
        ]
        assert "collect_source_file_mutation_data(mutant_names=" in inspect.getsource(mutmut_main._run)

    def test_stopped_after_stats_the_run_ends_where_the_mutants_would_start(self, shards, monkeypatch):
        import mutmut.__main__ as mutmut_main

        stock = mutmut_main.collect_source_file_mutation_data
        shards.stop_after_stats()
        shards.stop_after_stats()

        with pytest.raises(shards.StatsCollected):
            mutmut_main.collect_source_file_mutation_data(mutant_names=())
        shards.uninstall()
        assert mutmut_main.collect_source_file_mutation_data is stock

    @pytest.fixture
    def run(self, monkeypatch):
        """mutation_run, with every step it takes recorded instead of run - mutmut's run as `cli`."""
        monkeypatch.syspath_prepend(str(ROOT / "scripts"))
        run = _load("mutation_run")
        run.steps = []
        monkeypatch.setattr(
            run.mutation_shards, "install", lambda index, count: run.steps.append(("shard", index, count))
        )
        monkeypatch.setattr(run.mutation_shards, "stop_after_stats", lambda: run.steps.append(("stats only",)))
        monkeypatch.setattr(run.mutmut_decorators, "install", lambda: None)
        monkeypatch.setattr(run.mutation_cache, "refresh", lambda full: f"full={full}")
        return run

    def test_stats_only_ends_the_run_cleanly(self, run, monkeypatch):
        import mutmut.__main__ as mutmut_main

        def cli(args, standalone_mode):
            run.steps.append(("run", args))
            raise run.mutation_shards.StatsCollected

        monkeypatch.setattr(mutmut_main, "cli", cli)

        assert run.main(["--stats-only"]) == 0
        assert run.steps == [("stats only",), ("run", ["run"])]

    def test_stats_only_isnt_a_shard(self, run):
        with pytest.raises(SystemExit, match="isn't one of them"):
            run.main(["--stats-only", "--shard", "1/6"])

    def test_the_run_takes_its_shard_from_the_command_line(self, run, monkeypatch):
        import mutmut.__main__ as mutmut_main

        monkeypatch.setattr(mutmut_main, "cli", lambda args, standalone_mode: run.steps.append(("run", args)))

        assert run.main(["--shard", "3/6", "--full", "isik.common.*"]) == 0
        assert run.steps == [("shard", 3, 6), ("run", ["run", "isik.common.*"])]


def test_ci_runs_a_job_for_every_shard_and_gates_on_all_of_them():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/mutation.yml").read_text())
    count = workflow["env"]["MUTATION_SHARDS"]

    assert workflow["jobs"]["mutmut"]["strategy"] == {
        "fail-fast": False,
        "matrix": {"shard": list(range(1, count + 1))},
    }
    assert workflow["jobs"]["union"]["needs"] == "mutmut"
    # The map of tests is built once, and every shard starts from it.
    stats = workflow["jobs"]["stats"]["steps"]
    assert "--stats-only" in next(step["run"] for step in stats if step.get("name") == "Build the map of tests")
    handed = next(step for step in stats if step.get("uses", "").startswith("actions/upload-artifact"))["with"]
    assert handed["name"] == "mutmut-map"
    assert handed["path"].split() == ["mutants/mutmut-stats.json", "mutants/mutation-baseline.json"]
    assert workflow["jobs"]["mutmut"]["needs"] == "stats"
    taken = next(
        step for step in workflow["jobs"]["mutmut"]["steps"] if step.get("name") == "Start from the map of tests"
    )
    assert taken["with"] == {"name": "mutmut-map", "path": "mutants"}
    # Over the free plan's 20 concurrent jobs, a run would queue against itself - and the four test jobs run beside.
    assert count + 4 <= 20
