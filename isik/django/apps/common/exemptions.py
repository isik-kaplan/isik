"""
Every exemption a Django project declares, by rule - what `manage.py exemptions` prints. See
`isik.common.utils.exemptions` for the types themselves.
"""

import inspect
import os
from collections import Counter
from dataclasses import dataclass
from operator import attrgetter
from pathlib import Path

from django.apps import apps
from django.urls import get_resolver

from isik.common.utils.exemptions import (
    TEST_CODE,
    declared_exemptions,
    exemption_types,
    unimported_exemption_types,
    unseen_exemption_calls,
)
from isik.common.utils.strings import camel_to_snake
from isik.django.apps.common.urlconfs import project_urlconfs


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


def policy_rule(policy):
    """The rule a `RequestPolicy`'s `{action: reason}` exemptions are listed under: `policy.<its name>`."""
    return "policy." + camel_to_snake(policy.__name__).replace("_", "-")


def _source(cls):
    try:
        return inspect.getsourcefile(cls), inspect.getsourcelines(cls)[1]
    except (OSError, TypeError):
        return None, None


def _routed_policy_views(urlconf):
    try:
        from isik.django.drf.coverage import routed_actions
        from isik.django.drf.viewsets.request_policies import RequestPoliciesMixin
    except ImportError:  # pragma: no cover - DRF isn't installed
        return []
    # A dict keeps route order and lists a view routed twice once.
    return list(dict.fromkeys(r.view for r in routed_actions(urlconf) if issubclass(r.view, RequestPoliciesMixin)))


def _policy_entries(urlconf):
    entries, rules = [], {}
    for view in _routed_policy_views(urlconf):
        file, line = _source(view)
        for policy in view.request_policies:
            rule = policy_rule(policy)
            rules.setdefault(rule, policy)
            for action, reason in view.request_policy_exemptions(policy).items():
                entries.append(ExemptionEntry(rule, view.__qualname__, str(reason), file, line, action))
    return entries, rules


def _load_everything(urlconf):
    # Models are loaded by django.setup(); the urlconfs bring in every routed view, and so every
    # serializer, viewset and exemption declared with them.
    for each in project_urlconfs(urlconf):
        get_resolver(each).url_patterns  # noqa: B018 - loading it is the point


def project_exemptions(urlconf=None, *, include_library=False):
    """
    Every exemption the project declares, by rule - within one, in the order they were made, and a
    policy's in route order. It loads every urlconf the project serves (`project_urlconfs()`), or
    `urlconf` - one, or several in a list - to find them. The ones isik and other libraries make for
    themselves are left out unless `include_library`.
    """
    _load_everything(urlconf)
    typed = [
        ExemptionEntry(
            exemption.rule,
            type(exemption).__qualname__,
            exemption.reason,
            exemption.file,
            exemption.line,
            library=exemption.library,
        )
        for exemption in declared_exemptions(include_library=include_library)
    ]
    policies, _ = _policy_entries(urlconf)
    return sorted([*typed, *policies], key=attrgetter("rule"))


def project_exemption_rules(urlconf=None, *, include_library=False):
    """
    Each rule the project can be exempted from, why it's there, and how many exemptions it has - with
    the libraries' own counted only when `include_library`.
    """
    counts = Counter(entry.rule for entry in project_exemptions(urlconf, include_library=include_library))
    whys = {rule: cls.why for rule, cls in exemption_types().items()}
    for rule, policy in _policy_entries(urlconf)[1].items():
        whys[rule] = _policy_why(policy)
    return [ExemptionRule(rule, why, counts[rule]) for rule, why in sorted(whys.items())]


def _policy_why(policy):
    """A policy's own docstring's first paragraph, or else the message it refuses with."""
    doc = inspect.cleandoc(vars(policy).get("__doc__") or "")
    return " ".join(doc.split("\n\n")[0].split()) or str(policy.message)


def _project_paths():
    libraries = ("site-packages", "dist-packages")
    isik_root = str(Path(__file__).resolve().parents[3]) + os.sep
    return sorted(
        {
            config.path
            for config in apps.get_app_configs()
            if not any(part in libraries for part in Path(config.path).parts)
            and not str(Path(config.path).resolve()).startswith(isik_root)
        }
    )


def unseen_project_exemptions(urlconf=None, exclude=TEST_CODE):
    """
    `[(file, line, name)]` for each exemption call in the project's own apps that loading the project
    didn't run - one inside a function, say - so "every exemption" stays every one. See
    `unseen_exemption_calls()`.
    """
    _load_everything(urlconf)
    return unseen_exemption_calls(_project_paths(), exclude)


def unimported_project_exemption_types(urlconf=None, exclude=TEST_CODE):
    """
    `[(file, line, name)]` for each exemption type the project's own apps declare that loading the
    project didn't import, so its rule and its exemptions are missing from the listing. See
    `unimported_exemption_types()`.
    """
    _load_everything(urlconf)
    return unimported_exemption_types(_project_paths(), exclude)
