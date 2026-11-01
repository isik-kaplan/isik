"""
One shard of a mutation run, for CI to run the whole of it as several jobs at once.

    uv run python scripts/mutation_run.py --shard 2/6

A mutant's shard is a hash of its name, so there's no map of modules to shards to keep current: a module
added or deleted is in every shard's share already, each shard holds about as many mutants as the next,
and the same mutant lands in the same shard on every run and every machine. The shards are a partition -
every mutant in exactly one - so all of them passing is the whole run passing.

Every shard needs the same map of which tests reach which function, and building it means running the
whole suite traced. So CI builds it once - `--stats-only`, `stop_after_stats()` - and hands each shard the
`mutants/` files it lives in, which `mutation_cache.refresh()` then finds unchanged and mutmut loads.
"""

import zlib


def parse(shard):
    """`"2/6"` -> `(2, 6)`: the shard, counted from 1, and how many there are."""
    index, _, count = shard.partition("/")
    try:
        index, count = int(index), int(count)
    except ValueError:
        raise SystemExit(f"--shard takes INDEX/COUNT, like 2/6 - not {shard!r}.") from None
    if not 1 <= index <= count:
        raise SystemExit(f"--shard {shard}: the index is counted from 1 and is at most the count.")
    return index, count


def shard_of(mutant_name, count):
    """The shard `mutant_name` is in, counted from 1 - the same wherever and whenever it's asked."""
    return zlib.crc32(mutant_name.encode()) % count + 1


def install(index, count):
    """Narrow mutmut's run in this process to shard `index` of `count`. Idempotent; `uninstall()` undoes it."""
    import mutmut.__main__ as mutmut_main

    uninstall()
    collect = mutmut_main.collect_source_file_mutation_data

    def collect_source_file_mutation_data(*, mutant_names):
        mutants, by_path = collect(mutant_names=mutant_names)
        return [mutant for mutant in mutants if shard_of(mutant[1], count) == index], by_path

    collect_source_file_mutation_data.stock = collect
    mutmut_main.collect_source_file_mutation_data = collect_source_file_mutation_data


class StatsCollected(Exception):
    """Raised where mutmut would start on the mutants, once the map of tests is built and saved."""


def stop_after_stats():
    """End mutmut's run in this process once it has its map of tests - `StatsCollected`. `uninstall()` undoes it."""
    import mutmut.__main__ as mutmut_main

    uninstall()
    collect = mutmut_main.collect_source_file_mutation_data

    def collect_source_file_mutation_data(*, mutant_names):
        raise StatsCollected

    collect_source_file_mutation_data.stock = collect
    mutmut_main.collect_source_file_mutation_data = collect_source_file_mutation_data


def uninstall():
    """Put stock mutmut's collection back. Idempotent."""
    import mutmut.__main__ as mutmut_main

    stock = getattr(mutmut_main.collect_source_file_mutation_data, "stock", None)
    if stock is not None:
        mutmut_main.collect_source_file_mutation_data = stock
