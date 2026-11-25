"""
Every exemption a project declares, by rule, as text or JSON - what `isik exemptions` and `manage.py exemptions` print.
The exemptions themselves are `isik.common.utils.exemptions`'; a Django project's, its request policies' included, are
gathered by `isik.django.apps.common.exemptions`.
"""

import json
from collections import Counter
from dataclasses import dataclass
from operator import attrgetter
from pathlib import Path

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import declared_exemptions, exemption_types


@dataclass(frozen=True)
class ExemptionEntry:
    """
    One exemption: the rule it skips, what declared it, why, and where - `action` for a policy's own, and
    `library` for one a library made for itself (`makes_own_exemptions()`).
    """

    rule: str
    declared_by: str
    reason: str
    file: str | None
    line: int | None
    action: str | None = None
    library: bool = False


@dataclass(frozen=True)
class ExemptionRule:
    rule: str
    why: str
    count: int


def typed_exemptions(*, include_library=False):
    """Every exemption made so far through an exemption type - a request policy's are its own - by rule."""
    return sorted(
        (
            ExemptionEntry(
                exemption.rule,
                type(exemption).__qualname__,
                exemption.reason,
                exemption.file,
                exemption.line,
                library=exemption.library,
            )
            for exemption in declared_exemptions(include_library=include_library)
            if not exemption.listed_by_policy
        ),
        key=attrgetter("rule"),
    )


def exemption_rules(entries, whys=None):
    """Each rule, why it's there, and how many of `entries` it has: every exemption type's, and `whys`' too."""
    counts = Counter(entry.rule for entry in entries)
    found = {rule: cls.why for rule, cls in exemption_types().items() if not cls.listed_by_policy}
    found.update(whys or {})
    return [ExemptionRule(rule, why, counts[rule]) for rule, why in sorted(found.items())]


class UnknownRule(ValueError):
    """A rule asked for that no exemption type or policy exempts from."""


def of_rule(entries, rule, rules):
    """`entries`' exemptions of `rule` - one of `rules`, else `UnknownRule`."""
    if rule not in {each.rule for each in rules}:
        raise UnknownRule(_("No exemption type or policy exempts from %(rule)r.") % {"rule": rule})
    return [entry for entry in entries if entry.rule == rule]


def where(file, line):
    """`file:line`, relative to the working directory when it's under it."""
    if file is None:
        return "?"
    try:
        file = Path(file).relative_to(Path.cwd())
    except ValueError:
        pass
    return f"{file}:{line}"


def columns(rows):
    """Each row's cells padded to line up, the last left as is."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return ["  ".join([*(row[i].ljust(width) for i, width in enumerate(widths)), row[-1]]) for row in rows]


def listing(entries, unseen, unimported, format):
    """
    `(out, problems)`: the listing of `entries`, and a line for each exemption call (`unseen`) or type (`unimported`)
    loading the project missed - in `format`, "text" or "json", which carries them in the listing itself.
    """
    if format == "json":
        payload = {
            "exemptions": [
                {
                    "rule": entry.rule,
                    "declared_by": entry.declared_by,
                    "action": entry.action,
                    "reason": entry.reason,
                    "file": entry.file,
                    "line": entry.line,
                    "library": entry.library,
                }
                for entry in entries
            ],
            "unseen": [{"file": file, "line": line, "name": name} for file, line, name in unseen],
            "unimported_types": [{"file": file, "line": line, "name": name} for file, line, name in unimported],
        }
        return json.dumps(payload, indent=2), []
    rows = [
        (
            entry.rule,
            where(entry.file, entry.line),
            f"{entry.action} - {entry.reason}" if entry.action else entry.reason,
        )
        for entry in entries
    ]
    problems = [
        _("%(where)s makes an exemption through %(name)s that loading the project didn't - it isn't listed above.")
        % {"where": where(file, line), "name": name}
        for file, line, name in unseen
    ] + [
        _("%(where)s declares %(name)s, which loading the project didn't import - its exemptions aren't listed.")
        % {"where": where(file, line), "name": name}
        for file, line, name in unimported
    ]
    return "\n".join(columns(rows)) if rows else "", problems


def rules_listing(found, format):
    """Each rule of `found`, why it's there, and its count - in `format`, "text" or "json"."""
    if format == "json":
        payload = [{"rule": each.rule, "why": each.why, "count": each.count} for each in found]
        return json.dumps({"rules": payload}, indent=2)
    return "\n".join(columns([(each.rule, str(each.count), each.why) for each in found])) if found else ""


def incomplete(unseen, unimported):
    """Why a listing missing `unseen` calls or `unimported` types fails - None for a complete one."""
    if not unseen and not unimported:
        return None
    return _("%(count)d exemption call(s) or type(s) weren't loaded, so the listing isn't complete.") % {
        "count": len(unseen) + len(unimported)
    }


def report(entries, rules, unseen, unimported, *, rule=None, rules_only=False, format="text"):
    """
    `(out, problems, failure)` for a listing: of `rules` alone when `rules_only`, else of `entries` - only `rule`'s
    when given, one of `rules` or `UnknownRule` - with a line for each of `unseen` and `unimported`, and `failure`
    saying why the listing is incomplete, or None. Each of the four is a function, called only when needed.
    """
    if rules_only:
        return rules_listing(rules(), format), [], None
    found = entries()
    if rule is not None:
        found = of_rule(found, rule, rules())
    missed, untyped = unseen(), unimported()
    out, problems = listing(found, missed, untyped, format)
    return out, problems, incomplete(missed, untyped)
