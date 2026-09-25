"""
A pytest plugin for mutation runs only, loaded through `[tool.mutmut] pytest_add_cli_args`: runs each
mutant's tests in the order most likely to kill it soonest. mutmut stops at the first failing test
(`-x`), so every test ahead of the killer is time spent for nothing.

mutmut hands pytest a mutant's tests sorted fastest-first, but pytest-django then regroups every
database test ahead of every non-database one - so a pure unit test that would have killed the
mutant in a millisecond runs after all the slow API tests. This re-sorts, per mutant:

1. transactional database tests last - the one grouping that matters for correctness, since they
   flush the database for whatever follows;
2. tests that already killed a sibling mutant (same function) earlier in the run - a mutant's
   siblings are usually caught by the same test;
3. cheapest first, by mutmut's recorded durations;
4. most mutants killed across the run so far, then mutmut's own order.

Kills are appended to `MUTATION_KILLS` (every mutant runs in its own forked process, so a shared file
is how they learn from each other), each with the killer's rank in the order it ran in - which is
what `summarise()` reports, to show whether the ordering is doing its job. A function's mutants run
side by side on parallel workers, so they rarely get to learn from each other within one run; what
makes the difference is `MUTATION_KILLS_PRIOR`, the previous run's log, read but never written - CI
carries each run's log to the next. Keys are function and test names, stable across runs, and a stale
entry can only cost time, never a verdict:

    python -c "from tests.mutation_ordering import summarise; summarise('mutants/mutation-kills.jsonl')"

Outside a mutation run (`MUTANT_UNDER_TEST` unset, or one of mutmut's own non-mutant passes) it does
nothing. `MUTATION_ORDERING=off` keeps the kill log but leaves mutmut's order alone - for measuring
what the re-sort is worth.
"""

import collections
import json
import os
import statistics
from pathlib import Path

import pytest


KILLS = Path(os.environ.get("MUTATION_KILLS", "mutation-kills.jsonl"))
PRIOR = Path(os.environ.get("MUTATION_KILLS_PRIOR", "mutation-kills-prior.jsonl"))
STATS = Path(os.environ.get("MUTMUT_STATS", "mutmut-stats.json"))
# mutmut runs these passes through the same variable; none of them is a mutant.
NOT_A_MUTANT = {"", "fail", "stats", "mutant_generation", "list_all_tests"}

_order = []


def _mutant():
    mutant = os.environ.get("MUTANT_UNDER_TEST", "")
    return None if mutant in NOT_A_MUTANT else mutant


def _function(mutant):
    return mutant.rsplit("__mutmut_", 1)[0]


def _transactional(item):
    marker = item.get_closest_marker("django_db")
    if marker and (marker.kwargs.get("transaction") or marker.kwargs.get("reset_sequences")):
        return True
    return bool({"transactional_db", "live_server"} & set(getattr(item, "fixturenames", ())))


def _read_kills():
    rows = []
    for path in (PRIOR, KILLS):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:  # a line another process is still writing
                continue
    return rows


def ordering_key(function, kills, durations, position):
    """The sort key for one mutant's tests - see the module docstring for the order it produces."""
    siblings = collections.Counter(row["test"] for row in kills if row["function"] == function)
    overall = collections.Counter(row["test"] for row in kills)

    def key(item):
        return (
            _transactional(item),
            -siblings[item.nodeid],
            durations.get(item.nodeid, float("inf")),  # an unmeasured test is probably new - not instant
            -overall[item.nodeid],
            position[item.nodeid],
        )

    return key


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    mutant = _mutant()
    if mutant is None:
        return
    durations = json.loads(STATS.read_text()).get("duration_by_test", {}) if STATS.exists() else {}
    position = {item.nodeid: index for index, item in enumerate(items)}
    if os.environ.get("MUTATION_ORDERING") != "off":
        items.sort(key=ordering_key(_function(mutant), _read_kills(), durations, position))
    _order[:] = [item.nodeid for item in items]


def pytest_runtest_logreport(report):
    mutant = _mutant()
    if mutant is None or not report.failed:
        return
    row = {
        "function": _function(mutant),
        "mutant": mutant,
        "test": report.nodeid,
        "rank": _order.index(report.nodeid) + 1 if report.nodeid in _order else None,
        "of": len(_order),
        "seconds": round(report.duration, 3),
    }
    # One short write in append mode, so lines from concurrent mutant processes don't interleave.
    with KILLS.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def summarise(path=KILLS):
    """Where in the order each mutant's killing test sat - near 1 means the ordering is working."""
    first = {}
    for row in [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]:
        first.setdefault(row["mutant"], row)  # a mutant dies once; its first kill is the verdict
    ranks = [row["rank"] for row in first.values() if row["rank"]]
    print(f"{len(first)} killed mutants")
    print(f"  killing test's rank: median {statistics.median(ranks):.0f}, mean {statistics.mean(ranks):.1f}")
    print(f"  killed by the first test tried: {sum(rank == 1 for rank in ranks)}/{len(ranks)}")
    print(f"  needed more than five tests:    {sum(rank > 5 for rank in ranks)}/{len(ranks)}")
