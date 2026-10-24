"""
When a command asks its questions. A policy is called with `given` - `{key: Source}` for every setting an
option or environment variable already gave - and whether `--no-input` was passed, and says yes or no.
Any function of those two is a policy too.
"""

import sys

from isik.common.settings.declarations import Source


class TerminalUnlessNoInput:
    adds_no_input = True

    def __call__(self, given, no_input):
        return not no_input and sys.stdin.isatty() and sys.stdout.isatty()


class NoOptionGiven:
    adds_no_input = False

    def __init__(self, ignoring):
        self.ignoring = frozenset(ignoring)

    def __call__(self, given, no_input):
        return not any(
            source in (Source.OPTION, Source.OPTION_FILE) and key not in self.ignoring for key, source in given.items()
        )


class FunctionPolicy:
    """A plain `policy(given, no_input)` function - one that adds no `--no-input` of its own."""

    adds_no_input = False

    def __init__(self, function):
        self.function = function

    def __call__(self, given, no_input):
        return self.function(given, no_input)


def terminal_unless_no_input():
    """Asks in a terminal, unless `--no-input` or `<PREFIX>NO_INPUT` says not to - and adds both."""
    return TerminalUnlessNoInput()


def no_option_given(*, ignoring=()):
    """
    Asks when no setting came as an option. `ignoring` names the settings that don't count. An
    environment variable never counts: it was set before the command ran, not chosen for this run.
    """
    return NoOptionGiven(ignoring)
