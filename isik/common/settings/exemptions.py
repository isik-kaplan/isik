"""
The opt-outs a setting declares when it can't be reached one of the three ways - by option, by
environment variable, by question - each an `Exemption`, so it says why and is listed with the rest.
"""

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption


class NoOption(
    Exemption,
    rule="settings.option",
    why="Every setting can be given as a command-line option.",
):
    pass


class NoEnv(
    Exemption,
    rule="settings.environment-variable",
    why="Every setting can be given as an environment variable.",
):
    pass


class NoQuestion(
    Exemption,
    rule="settings.question",
    why="Every setting is asked for when a command runs interactively.",
):
    pass


class AskedAtUse(
    Exemption,
    rule="settings.question.asked-at-use",
    why="Every setting is asked for with the others; one asked only where it's used must say so.",
):
    pass


class ReplacedByFlow(
    Exemption,
    rule="settings.question.replaced-by-flow",
    why="Every setting is asked for with the others; one a hand-written flow asks for instead must say so.",
):
    """`question=ReplacedByFlow(flow=ask_for_locks, reason=...)` - `flow` is the app's own; isik never calls it."""

    def __new__(cls, *, flow, reason):
        if not callable(flow):
            raise TypeError(_("ReplacedByFlow's flow must be the function that asks, not %(value)r.") % {"value": flow})
        self = super().__new__(cls, reason=reason)
        self.flow = flow
        return self


class IsConstant(
    Exemption,
    rule="settings.constant",
    why="A setting is something a user can set; a fixed value must say why it's fixed.",
):
    pass


# Any of these stands in for the question.
QUESTION_EXEMPTIONS = (NoQuestion, AskedAtUse, ReplacedByFlow)
