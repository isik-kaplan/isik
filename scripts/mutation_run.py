"""
`mutmut run`, with decorated functions mutated too - see `mutmut_decorators.py` - and `mutants/` kept
between runs, so a run re-tests only what a change could have affected - see `mutation_cache.py`.

    uv run python scripts/mutation_run.py                 # every mutant a change could have affected
    uv run python scripts/mutation_run.py --full          # every mutant, from nothing
    uv run python scripts/mutation_run.py "isik.common.*" # some
    uv run python scripts/mutation_run.py --shard 2/6     # one of six, as CI runs them - see mutation_shards.py

Run by path, not `-m scripts.mutation_run`, so `scripts` isn't imported as a package from this
checkout into every child mutmut forks from it.
"""

import sys

import mutation_cache
import mutation_shards
import mutmut_decorators


def main(argv):
    argv = list(argv)
    full = "--full" in argv
    if full:
        argv.remove("--full")
    if "--shard" in argv:
        at = argv.index("--shard")
        mutation_shards.install(*mutation_shards.parse(argv[at + 1]))
        del argv[at : at + 2]
    mutmut_decorators.install()
    print(mutation_cache.refresh(full=full))
    from mutmut.__main__ import cli

    return cli(["run", *argv], standalone_mode=False) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
