"""
`isik.common.settings` on a click command - options, environment variables and questions from one
declaration. Needs the `cli` extra: `pip install isik[cli]`.

    @options(Backup)
    @click.command()
    def main(settings: Backup): ...
"""

from isik._internal.checks import check_extra


check_extra("cli", "click")
check_extra("cli", "questionary")

from isik.cli.commands import options  # noqa: E402
from isik.cli.help import HelpFormat  # noqa: E402
from isik.cli.policies import no_option_given, terminal_unless_no_input  # noqa: E402


__all__ = ["HelpFormat", "no_option_given", "options", "terminal_unless_no_input"]
