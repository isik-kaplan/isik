from django.core.management.base import BaseCommand, CommandError

from isik.common.utils.exemption_listing import UnknownRule, report
from isik.django.apps.common.exemptions import (
    project_exemption_rules,
    project_exemptions,
    unimported_project_exemption_types,
    unseen_project_exemptions,
)


class Command(BaseCommand):
    help = (
        "Lists every exemption the project declares, by the rule it skips, with where and why. Fails when "
        "loading the project missed an exemption call or type, so the listing is incomplete."
    )

    def add_arguments(self, parser):
        parser.add_argument("--rule", help="Only this rule's exemptions.")
        parser.add_argument("--rules", action="store_true", help="Each rule, why it's there, and its count.")
        parser.add_argument("--format", choices=["text", "json"], default="text")
        parser.add_argument(
            "--urlconf",
            action="append",
            help="A urlconf to load views from - repeat it for several. Every urlconf the project serves by default.",
        )
        parser.add_argument(
            "--include-library",
            action="store_true",
            help="Also the exemptions isik and other libraries make for themselves.",
        )

    def handle(self, *args, rule, rules, format, urlconf, include_library, **options):
        try:
            out, problems, failure = django_report(urlconf, include_library, rule=rule, rules_only=rules, format=format)
        except UnknownRule as unknown:
            raise CommandError(str(unknown)) from None
        if out:
            self.stdout.write(out)
        for problem in problems:
            self.stderr.write(problem)
        # A listing missing some of the project's exemptions fails, so a CI step running it is a gate
        # and not only a report. Each one is named above.
        if failure is not None:
            raise CommandError(failure)


def django_report(urlconf, include_library, **options):
    """`report()` of the Django project's exemptions - `manage.py exemptions`', and `isik exemptions`' in one."""
    return report(
        lambda: project_exemptions(urlconf, include_library=include_library),
        lambda: project_exemption_rules(urlconf, include_library=include_library),
        lambda: unseen_project_exemptions(urlconf),
        lambda: unimported_project_exemption_types(urlconf),
        **options,
    )
