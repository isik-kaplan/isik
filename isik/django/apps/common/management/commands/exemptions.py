import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from isik._internal.translation import gettext as _
from isik.django.apps.common.exemptions import (
    project_exemption_rules,
    project_exemptions,
    unimported_project_exemption_types,
    unseen_project_exemptions,
)


def _where(file, line):
    """`file:line`, relative to the working directory when it's under it."""
    if file is None:
        return "?"
    try:
        file = Path(file).relative_to(Path.cwd())
    except ValueError:
        pass
    return f"{file}:{line}"


def _columns(rows):
    """Each row's cells padded to line up, the last left as is."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return ["  ".join([*(row[i].ljust(width) for i, width in enumerate(widths)), row[-1]]) for row in rows]


class Command(BaseCommand):
    help = "Lists every exemption the project declares, by the rule it skips, with where and why."

    def add_arguments(self, parser):
        parser.add_argument("--rule", help="Only this rule's exemptions.")
        parser.add_argument("--rules", action="store_true", help="Each rule, why it's there, and its count.")
        parser.add_argument("--format", choices=["text", "json"], default="text")
        parser.add_argument(
            "--urlconf",
            action="append",
            help="A urlconf to load views from - repeat it for several. Every urlconf the project serves by default.",
        )

    def handle(self, *args, rule, rules, format, urlconf, **options):
        if rules:
            self._rules(project_exemption_rules(urlconf), format)
            return
        entries = project_exemptions(urlconf)
        if rule is not None:
            known = {each.rule for each in project_exemption_rules(urlconf)}
            if rule not in known:
                raise CommandError(_("No exemption type or policy exempts from %(rule)r.") % {"rule": rule})
            entries = [entry for entry in entries if entry.rule == rule]
        unseen = unseen_project_exemptions(urlconf)
        unimported = unimported_project_exemption_types(urlconf)
        if format == "json":
            self.stdout.write(
                json.dumps(
                    {
                        "exemptions": [
                            {
                                "rule": entry.rule,
                                "declared_by": entry.declared_by,
                                "action": entry.action,
                                "reason": entry.reason,
                                "file": entry.file,
                                "line": entry.line,
                            }
                            for entry in entries
                        ],
                        "unseen": [{"file": file, "line": line, "name": name} for file, line, name in unseen],
                        "unimported_types": [
                            {"file": file, "line": line, "name": name} for file, line, name in unimported
                        ],
                    },
                    indent=2,
                )
            )
            return
        rows = [
            (
                entry.rule,
                _where(entry.file, entry.line),
                f"{entry.action} - {entry.reason}" if entry.action else entry.reason,
            )
            for entry in entries
        ]
        if rows:
            self.stdout.write("\n".join(_columns(rows)))
        for file, line, name in unseen:
            self.stderr.write(
                _(
                    "%(where)s makes an exemption through %(name)s that loading the project didn't - it isn't "
                    "listed above."
                )
                % {"where": _where(file, line), "name": name}
            )
        for file, line, name in unimported:
            self.stderr.write(
                _(
                    "%(where)s declares %(name)s, which loading the project didn't import - its exemptions aren't "
                    "listed."
                )
                % {"where": _where(file, line), "name": name}
            )

    def _rules(self, found, format):
        if format == "json":
            payload = [{"rule": each.rule, "why": each.why, "count": each.count} for each in found]
            self.stdout.write(json.dumps({"rules": payload}, indent=2))
            return
        if found:
            self.stdout.write("\n".join(_columns([(each.rule, str(each.count), each.why) for each in found])))
