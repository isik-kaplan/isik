"""
`mutmut run`, with decorated functions mutated too - see `mutmut_decorators.py`.

    uv run python scripts/mutation_run.py                 # every mutant
    uv run python scripts/mutation_run.py "isik.common.*" # some

Run by path, not `-m scripts.mutation_run`, so `scripts` isn't imported as a package from this
checkout into every child mutmut forks from it.
"""

import sys

import mutmut_decorators


def main(argv):
    mutmut_decorators.install()
    from mutmut.__main__ import cli

    return cli(["run", *argv], standalone_mode=False) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
