"""
Keep `mutants/` between runs, and throw away only what a change could have made wrong.

mutmut keeps a stored verdict wherever the function it mutates is unchanged, re-traces only tests it
has never seen, and copies a file into `mutants/` only when no copy is there. Run after run that is
fast, and wrong in four ways that all read as a clean or an ordinary result:

- A test edited in place keeps its old trace - mutmut knows tests by name, not by content - and every
  kill it earned is kept, earned or not. A weakened test leaves its kills standing.
- A changed module is never re-traced either. A function the map has no tests for is marked "no tests"
  without running anything, so new code reached by an existing test reads as untested, and an
  unchanged function a new caller reaches keeps whatever it was marked before.
- A kill earned through code that has since changed is kept: mutmut only re-tests a function whose own
  body changed, never one whose callee did.
- A deleted file's copy stays in `mutants/`, where the suite runs, so a deleted test still runs and a
  deleted module still imports.

So before each run this compares every file that can reach a run - what mutmut copies into `mutants/`
and what decides how it mutates - against a hash of each taken before the last one, and then:

- Any change resets every stored verdict that is not a kill. That costs almost nothing: mutmut marks a
  mutant whose function still has no tests "no tests" again without running it, and survivors and
  timeouts are what a run is for. Narrowing this is how an unchanged function's stale "no tests"
  survives a change that gave it tests.
- A changed or deleted test file, and a changed or deleted source module, make every test that touched
  them stale: those tests leave the stats map, so mutmut re-traces them, and every kill whose function
  any of them reached is reset.
- A deleted file's copy goes from `mutants/`, and a changed module's copy too, so mutmut regenerates it
  whatever the file times say. Its `.meta` stays: that is where mutmut keeps each unchanged function's
  verdict.
- Anything else that changed - shared test code, settings, migrations, a non-Python file the package
  carries, the dependencies, the config, this tooling - can change what any test does, so the whole of
  `mutants/` goes and the run starts cold. So does a missing baseline or stats map, and `--full`.

What it still can't see: a test that reaches new code only through something the trace doesn't
record - module-level code, or a framework calling a function registered at import time - without
touching any changed function. Its new code reads as "no tests" until the next full run, which errs
toward a run that is red, never toward one that is green.
"""

import fnmatch
import hashlib
import json
import shutil
import tomllib
from pathlib import Path

from mutmut.utils.format_utils import get_module_from_key, mangled_name_from_mutant_name


BASELINE = "mutation-baseline.json"
STATS = "mutmut-stats.json"
# mutmut's own reading of its exit codes - mutmut.stats.status_by_exit_code - for the two it calls killed.
KILLED = frozenset({1, 3})


def _mutmut_config(root):
    return tomllib.loads((root / "pyproject.toml").read_text())["tool"]["mutmut"]


def watched(root):
    """What can change a run: the sources and tests mutmut copies, the config, the lockfile, this tooling."""
    return [*_mutmut_config(root)["source_paths"], "tests", "scripts", "pyproject.toml", "uv.lock"]


def fingerprint(root):
    """{path relative to root: content hash} for every watched file, bytecode caches left out."""
    hashes = {}
    for entry in watched(root):
        base = root / entry
        for path in [base] if base.is_file() else sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def is_source_module(root, path):
    """A file mutmut mutates: Python under a source path that `do_not_mutate` doesn't exclude."""
    config = _mutmut_config(root)
    return (
        path.endswith(".py")
        and any(path.startswith(f"{source}/") for source in config["source_paths"])
        and not any(fnmatch.fnmatch(path, pattern) for pattern in config.get("do_not_mutate", []))
    )


def is_test_module(path):
    """A file of tests pytest collects - anything else under tests/ is shared by them."""
    return path.startswith("tests/") and Path(path).name.startswith("test_") and path.endswith(".py")


def module_of(path):
    """The dotted module a source file is - the prefix of its functions' keys in the stats map."""
    module = Path(path).with_suffix("").as_posix().replace("/", ".")
    return module.removesuffix(".__init__")


def _start_cold(mutants, current, reason):
    shutil.rmtree(mutants, ignore_errors=True)
    mutants.mkdir()
    (mutants / BASELINE).write_text(json.dumps(current, indent=4, sort_keys=True))
    return f"full run: {reason}"


def _remove_copies(mutants, paths):
    """Remove each path's copy and mutmut's data for it, then any directory that leaves empty."""
    for path in paths:
        for copy in (mutants / path, mutants / f"{path}.meta", mutants / f"{path}.spans"):
            copy.unlink(missing_ok=True)
        parent = (mutants / path).parent
        while parent != mutants and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def _reset_verdicts(mutants, traced, stale_tests):
    """Unset every verdict that isn't a kill, and every kill reached by a stale test. Returns the count."""
    reset = 0
    for meta_path in sorted(mutants.rglob("*.meta")):
        meta = json.loads(meta_path.read_text())
        verdicts = meta["exit_code_by_key"]
        stale = [
            key
            for key, exit_code in verdicts.items()
            if exit_code is not None
            # The same lookup mutmut makes for a mutant's tests in `_run`.
            and (
                exit_code not in KILLED
                or traced.get(mangled_name_from_mutant_name(key.replace("__init__.", "")), set()) & stale_tests
            )
        ]
        if stale:
            verdicts.update(dict.fromkeys(stale))
            meta_path.write_text(json.dumps(meta, indent=4))
            reset += len(stale)
    return reset


def refresh(root=Path("."), *, full=False):
    """Make `mutants/` safe for a bare `mutmut run` to reuse, and say what was kept."""
    mutants = root / "mutants"
    current = fingerprint(root)
    if full:
        return _start_cold(mutants, current, "asked for")
    try:
        stored = json.loads((mutants / BASELINE).read_text())
        stats = json.loads((mutants / STATS).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return _start_cold(mutants, current, "no earlier run to build on")

    changed = sorted(path for path in stored.keys() | current.keys() if stored.get(path) != current.get(path))
    if not changed:
        return "nothing changed: every stored verdict stands"
    shared = [path for path in changed if not is_source_module(root, path) and not is_test_module(path)]
    if shared:
        return _start_cold(mutants, current, f"{shared[0]} changed, which any test can depend on")

    changed_tests = {path for path in changed if is_test_module(path)}
    changed_modules = {module_of(path) for path in changed if is_source_module(root, path)}
    traced = {key: set(tests) for key, tests in stats["tests_by_mangled_function_name"].items()}
    known_tests = set(stats["duration_by_test"]).union(*traced.values())
    stale_tests = {test for test in known_tests if test.partition("::")[0] in changed_tests}
    stale_tests |= {
        test for key, tests in traced.items() if get_module_from_key(key) in changed_modules for test in tests
    }

    _remove_copies(mutants, [path for path in changed if path not in current])
    for path in changed:
        if path in current and is_source_module(root, path):
            (mutants / path).unlink(missing_ok=True)
    reset = _reset_verdicts(mutants, traced, stale_tests)

    # Dropped from both: mutmut re-traces a test missing from duration_by_test, and a re-trace only adds
    # to tests_by_mangled_function_name - the stale test's old edges would stay beside its new ones.
    stats["tests_by_mangled_function_name"] = {key: sorted(tests - stale_tests) for key, tests in traced.items()}
    stats["duration_by_test"] = {
        test: duration for test, duration in stats["duration_by_test"].items() if test not in stale_tests
    }
    (mutants / STATS).write_text(json.dumps(stats, indent=4))
    (mutants / BASELINE).write_text(json.dumps(current, indent=4, sort_keys=True))
    return (
        f"{len(changed)} file(s) changed: {reset} verdict(s) reset, "
        f"{len(stale_tests)} test(s) to re-trace, every other verdict kept"
    )
