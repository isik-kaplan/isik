"""The mutation-run ordering plugin: what order it produces, and that it stays out of normal runs."""

import json
from types import SimpleNamespace

import pytest

from tests import mutation_ordering


def item(nodeid, *, transaction=False, fixtures=()):
    marker = SimpleNamespace(kwargs={"transaction": True}) if transaction else None
    return SimpleNamespace(nodeid=nodeid, get_closest_marker=lambda name: marker, fixturenames=list(fixtures))


@pytest.fixture
def logs(tmp_path, monkeypatch):
    kills, prior, stats = tmp_path / "kills.jsonl", tmp_path / "prior.jsonl", tmp_path / "stats.json"
    monkeypatch.setattr(mutation_ordering, "KILLS", kills)
    monkeypatch.setattr(mutation_ordering, "PRIOR", prior)
    monkeypatch.setattr(mutation_ordering, "STATS", stats)
    return SimpleNamespace(kills=kills, prior=prior, stats=stats)


def write_rows(path, *rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def order(monkeypatch, items, mutant="pkg.mod.x_func__mutmut_3"):
    monkeypatch.setenv("MUTANT_UNDER_TEST", mutant)
    mutation_ordering.pytest_collection_modifyitems(items)
    return [each.nodeid for each in items]


class TestOrdering:
    def test_cheapest_first_by_recorded_duration(self, logs, monkeypatch):
        logs.stats.write_text(json.dumps({"duration_by_test": {"slow": 2.0, "fast": 0.1, "mid": 0.5}}))
        assert order(monkeypatch, [item("slow"), item("fast"), item("mid")]) == ["fast", "mid", "slow"]

    def test_an_unmeasured_test_goes_after_measured_ones(self, logs, monkeypatch):
        logs.stats.write_text(json.dumps({"duration_by_test": {"known": 5.0}}))
        assert order(monkeypatch, [item("new"), item("known")]) == ["known", "new"]

    def test_a_sibling_killer_goes_first_even_when_slower(self, logs, monkeypatch):
        logs.stats.write_text(json.dumps({"duration_by_test": {"killer": 3.0, "cheap": 0.1}}))
        write_rows(logs.kills, {"function": "pkg.mod.x_func", "test": "killer"})
        assert order(monkeypatch, [item("cheap"), item("killer")]) == ["killer", "cheap"]

    def test_a_kill_in_another_function_only_breaks_duration_ties(self, logs, monkeypatch):
        logs.stats.write_text(json.dumps({"duration_by_test": {"a": 0.1, "b": 0.1, "slow": 1.0}}))
        write_rows(
            logs.kills, {"function": "pkg.mod.x_other", "test": "b"}, {"function": "pkg.mod.x_other", "test": "slow"}
        )
        assert order(monkeypatch, [item("a"), item("slow"), item("b")]) == ["b", "a", "slow"]

    def test_the_previous_runs_log_counts_too(self, logs, monkeypatch):
        logs.stats.write_text(json.dumps({"duration_by_test": {"killer": 3.0, "cheap": 0.1}}))
        write_rows(logs.prior, {"function": "pkg.mod.x_func", "test": "killer"})
        assert order(monkeypatch, [item("cheap"), item("killer")]) == ["killer", "cheap"]

    def test_transactional_tests_stay_last(self, logs, monkeypatch):
        logs.stats.write_text(json.dumps({"duration_by_test": {"tx": 0.0, "fixture_tx": 0.0, "plain": 9.0}}))
        write_rows(logs.kills, {"function": "pkg.mod.x_func", "test": "tx"})
        items = [item("tx", transaction=True), item("fixture_tx", fixtures=["transactional_db"]), item("plain")]
        assert order(monkeypatch, items)[0] == "plain"

    def test_mutmuts_own_order_breaks_the_remaining_ties(self, logs, monkeypatch):
        assert order(monkeypatch, [item("first"), item("second"), item("third")]) == ["first", "second", "third"]

    def test_a_half_written_line_is_skipped(self, logs, monkeypatch):
        logs.kills.write_text('{"function": "pkg.mod.x_func", "test": "killer"}\n{"function": "pkg.mo')
        logs.stats.write_text(json.dumps({"duration_by_test": {"killer": 3.0, "cheap": 0.1}}))
        assert order(monkeypatch, [item("cheap"), item("killer")]) == ["killer", "cheap"]

    def test_ordering_off_keeps_mutmuts_order(self, logs, monkeypatch):
        logs.stats.write_text(json.dumps({"duration_by_test": {"slow": 2.0, "fast": 0.1}}))
        monkeypatch.setenv("MUTATION_ORDERING", "off")
        assert order(monkeypatch, [item("slow"), item("fast")]) == ["slow", "fast"]


class TestOutsideMutationRuns:
    @pytest.mark.parametrize("value", [None, "", "fail", "stats", "mutant_generation", "list_all_tests"])
    def test_does_nothing(self, logs, monkeypatch, value):
        logs.stats.write_text(json.dumps({"duration_by_test": {"slow": 2.0, "fast": 0.1}}))
        if value is None:
            monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
        else:
            monkeypatch.setenv("MUTANT_UNDER_TEST", value)
        items = [item("slow"), item("fast")]
        mutation_ordering.pytest_collection_modifyitems(items)
        mutation_ordering.pytest_runtest_logreport(SimpleNamespace(failed=True, nodeid="slow", duration=1.0))
        assert [each.nodeid for each in items] == ["slow", "fast"]
        assert not logs.kills.exists()


class TestKillLog:
    def test_a_failure_is_logged_with_its_rank(self, logs, monkeypatch):
        order(monkeypatch, [item("a"), item("b")], mutant="pkg.mod.x_func__mutmut_7")
        mutation_ordering.pytest_runtest_logreport(SimpleNamespace(failed=True, nodeid="b", duration=0.25))
        mutation_ordering.pytest_runtest_logreport(SimpleNamespace(failed=False, nodeid="a", duration=0.1))
        rows = [json.loads(line) for line in logs.kills.read_text().splitlines()]
        assert rows == [
            {
                "function": "pkg.mod.x_func",
                "mutant": "pkg.mod.x_func__mutmut_7",
                "test": "b",
                "rank": 2,
                "of": 2,
                "seconds": 0.25,
            }
        ]

    def test_summarise_reports_the_first_kill_per_mutant(self, logs, capsys):
        write_rows(
            logs.kills,
            {"mutant": "m1", "rank": 1},
            {"mutant": "m1", "rank": 9},  # a later failure of the same mutant isn't the verdict
            {"mutant": "m2", "rank": 7},
        )
        mutation_ordering.summarise(logs.kills)
        out = capsys.readouterr().out
        assert "2 killed mutants" in out
        assert "killed by the first test tried: 1/2" in out
        assert "needed more than five tests:    1/2" in out
