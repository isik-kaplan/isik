"""
isik's own system checks - each on by default, and each tuned or turned off in `ISIK_CHECKS`, named
by the enums here rather than by strings:

    from isik.django.apps.common.checks import Check, CheckOption, Severity

    ISIK_CHECKS = {
        Check.DOCUMENTATION: {CheckOption.APPS: ["billing", "accounts"]},
        Check.IDEMPOTENCY: {CheckOption.URLCONFS: ["config.urls.api"]},
        Check.NOT_ATOMIC: {CheckOption.SEVERITY: Severity.WARNING},
        Check.HISTORY_INDEXES: {CheckOption.ENABLED: False},
    }

Every check takes `CheckOption.ENABLED` (default True) and `CheckOption.SEVERITY` (a `Severity`,
default the check's own). A check over routed views takes `CheckOption.URLCONFS` - every urlconf the
project serves by default (`project_urlconfs()`), and a warning, `isik.W003`, names each served one
it is told to skip. A check over models takes `CheckOption.APPS`, app labels - the project's own apps
by default (`project_app_configs()`). `Check.DOCUMENTATION` also takes `CheckOption.EXEMPTION_TYPES`,
the `Exemption` types (or their dotted paths) that answer for a field, and
`CheckOption.INHERITED_FROM_LIBRARIES` - True by default; False leaves out the fields a model inherits
unchanged from a library's abstract base. Anything else in `ISIK_CHECKS` - a string where an enum
belongs included - is an error, `isik.E012`.

| check                      | id                     | what it refuses                                              |
|----------------------------|------------------------|--------------------------------------------------------------|
| Check.VALIDATORS           | isik.E001              | a validator that doesn't say whether it holds at the column  |
| Check.CONCURRENCY_TOKENS   | isik.E002              | a precondition view comparing a token a write can keep       |
| Check.PRECONDITION_OPTIONS | isik.E003, isik.W001   | precondition options that are invalid or undo each other    |
| Check.RELATION_LOCKS       | isik.W002              | a covered relation whose lock trigger isn't installed        |
| Check.VERSION_READS        | isik.E004              | a write asking If-Match with no read handing out the version |
| Check.IDEMPOTENCY          | isik.E005              | a routed write that honors no Idempotency-Key                |
| Check.SINGLETONS           | isik.E006              | a flattened singleton no unique constraint proves            |
| Check.PRECONDITIONS        | isik.E007              | a routed write to one object that asks no If-Match           |
| Check.HISTORY_INDEXES      | isik.E008              | an event model without its (pgh_obj, -pgh_id) index          |
| Check.EXEMPTIONS           | isik.E009              | an exemption loading the project didn't see                  |
| Check.DOCUMENTATION        | isik.E010              | a field without help_text or db_comment                      |
| Check.NOT_ATOMIC           | isik.E011              | a view outside ATOMIC_REQUESTS that doesn't say why          |
| Check.KEPT_FIELD_CONDITIONS | isik.E013             | a trigger condition comparing version or updated_at          |
"""

import functools
from enum import Enum

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _


# Plain enums, not StrEnums: a string never equals a member, so a string in ISIK_CHECKS is refused
# rather than quietly taken.
class Check(Enum):
    """Each of isik's system checks - a key of `ISIK_CHECKS`."""

    VALIDATORS = "validators"
    CONCURRENCY_TOKENS = "concurrency_tokens"
    PRECONDITION_OPTIONS = "precondition_options"
    RELATION_LOCKS = "relation_locks"
    VERSION_READS = "version_reads"
    IDEMPOTENCY = "idempotency"
    SINGLETONS = "singletons"
    PRECONDITIONS = "preconditions"
    HISTORY_INDEXES = "history_indexes"
    EXEMPTIONS = "exemptions"
    DOCUMENTATION = "documentation"
    NOT_ATOMIC = "not_atomic"
    KEPT_FIELD_CONDITIONS = "kept_field_conditions"


class CheckOption(Enum):
    """What a check's `ISIK_CHECKS` entry sets - the keys of its dict."""

    ENABLED = "enabled"
    SEVERITY = "severity"
    URLCONFS = "urlconfs"
    APPS = "apps"
    EXEMPTION_TYPES = "exemption_types"
    INHERITED_FROM_LIBRARIES = "inherited_from_libraries"


class Severity(Enum):
    """What a check's messages are reported as - `CheckOption.SEVERITY`'s value."""

    ERROR = "error"
    WARNING = "warning"


SEVERITIES = {Severity.ERROR: Error, Severity.WARNING: Warning}

# {check: the options it takes beyond ENABLED and SEVERITY}
CHECKS = {
    Check.VALIDATORS: (),
    Check.CONCURRENCY_TOKENS: (CheckOption.URLCONFS,),
    Check.PRECONDITION_OPTIONS: (CheckOption.URLCONFS,),
    Check.RELATION_LOCKS: (),
    Check.VERSION_READS: (CheckOption.URLCONFS,),
    Check.IDEMPOTENCY: (CheckOption.URLCONFS,),
    Check.SINGLETONS: (),
    Check.PRECONDITIONS: (CheckOption.URLCONFS,),
    Check.HISTORY_INDEXES: (CheckOption.APPS,),
    Check.EXEMPTIONS: (CheckOption.URLCONFS,),
    Check.DOCUMENTATION: (CheckOption.APPS, CheckOption.EXEMPTION_TYPES, CheckOption.INHERITED_FROM_LIBRARIES),
    Check.NOT_ATOMIC: (CheckOption.URLCONFS,),
    Check.KEPT_FIELD_CONDITIONS: (CheckOption.APPS,),
}

DEFAULTS = {
    CheckOption.ENABLED: True,
    CheckOption.SEVERITY: None,
    CheckOption.URLCONFS: None,
    CheckOption.APPS: None,
    CheckOption.EXEMPTION_TYPES: None,
    CheckOption.INHERITED_FROM_LIBRARIES: True,
}


def _configured():
    return getattr(settings, "ISIK_CHECKS", None) or {}


def check_options(check):
    """The options `check` runs with: its `ISIK_CHECKS` entry over the defaults."""
    given = _configured().get(check)
    return {**DEFAULTS, **given} if isinstance(given, dict) else dict(DEFAULTS)


def isik_check(check, *tags):
    """
    Registers `function(options)` as `check`: skipped when `ISIK_CHECKS` turns it off, and its messages
    given the severity `ISIK_CHECKS` sets, keeping their ids.
    """

    def decorate(function):
        @register(*tags)
        @functools.wraps(function)
        def registered(app_configs, **kwargs):
            return run_check(check, function, **kwargs)

        return registered

    return decorate


def run_check(check, function, **kwargs):
    """Runs `function(options, **kwargs)` as `check` - see `isik_check()`."""
    options = check_options(check)
    if not options[CheckOption.ENABLED]:
        return []
    messages = function(options, **kwargs)
    severity = SEVERITIES.get(options[CheckOption.SEVERITY])
    if severity is None:
        return messages
    return [severity(message.msg, hint=message.hint, obj=message.obj, id=message.id) for message in messages]


def _project_configs(options):
    from isik.django.apps.common.project import project_app_configs

    return project_app_configs(options[CheckOption.APPS])


def _views_installed():
    return apps.is_installed("rest_framework")


def _as_member(value, enum):
    """The member a string names, by its value or its name - to say which to write instead."""
    return next((member for member in enum if value in (member.value, member.name)), None)


def _key_problem(key, enum, where):
    """Why `key` isn't a member of `enum` - a string naming one, or nothing of it at all."""
    member = _as_member(key, enum)
    if member is not None:
        return _("%(where)s names %(key)r as a string - write %(member)s.") % {
            "where": where,
            "key": key,
            "member": f"{enum.__name__}.{member.name}",
        }
    return _("%(where)s names %(key)r, which isn't a %(enum)s: %(members)s.") % {
        "where": where,
        "key": key,
        "enum": enum.__name__,
        "members": ", ".join(f"{enum.__name__}.{member.name}" for member in enum),
    }


def _entry_problems(check, options):
    where = f"ISIK_CHECKS[Check.{check.name}]"
    problems = []
    takes = (CheckOption.ENABLED, CheckOption.SEVERITY, *CHECKS[check])
    for option in options:
        if not isinstance(option, CheckOption):
            problems.append(_key_problem(option, CheckOption, where))
        elif option not in takes:
            problems.append(
                _("%(where)s has CheckOption.%(option)s - it takes %(takes)s.")
                % {
                    "where": where,
                    "option": option.name,
                    "takes": ", ".join(f"CheckOption.{each.name}" for each in takes),
                }
            )
    severity = options.get(CheckOption.SEVERITY)
    if severity is not None and not isinstance(severity, Severity):
        member = _as_member(severity, Severity)
        problems.append(
            _("%(where)s's severity is %(value)r - write %(choices)s.")
            % {
                "where": where,
                "value": severity,
                "choices": f"Severity.{member.name}"
                if member is not None
                else ", ".join(f"Severity.{each.name}" for each in Severity),
            }
        )
    return problems


def _blind_spots(check, options):
    from isik.django.apps.common.urlconfs import project_urlconfs

    given = options.get(CheckOption.URLCONFS)
    if given is None or not options.get(CheckOption.ENABLED, True):
        return []
    checked = project_urlconfs(given)
    skipped = [urlconf for urlconf in project_urlconfs() if urlconf not in checked]
    if not skipped:
        return []
    return [
        Warning(
            _("ISIK_CHECKS[Check.%(name)s] leaves served urlconfs unchecked: %(urlconfs)s.")
            % {"name": check.name, "urlconfs": ", ".join(skipped)},
            hint=_("Leave out CheckOption.URLCONFS to check every urlconf the project serves."),
            id="isik.W003",
        )
    ]


@register()
def isik_checks_are_configured(app_configs, **kwargs):
    """
    `ISIK_CHECKS` names only checks, options and severities there are, each by its enum - and says when
    it leaves served urlconfs unchecked.
    """
    configured = _configured()
    if not isinstance(configured, dict):
        return [Error(_("ISIK_CHECKS is %(value)r, not a dict.") % {"value": configured}, id="isik.E012")]
    hint = _("See isik.django.apps.common.checks.")
    problems = []
    warnings = []
    for check, options in configured.items():
        if not isinstance(check, Check):
            problems.append(_key_problem(check, Check, "ISIK_CHECKS"))
        elif not isinstance(options, dict):
            problems.append(
                _("ISIK_CHECKS[Check.%(name)s] is %(value)r, not a dict.") % {"name": check.name, "value": options}
            )
        else:
            problems.extend(_entry_problems(check, options))
            warnings.extend(_blind_spots(check, options))
    return [Error(problem, hint=hint, id="isik.E012") for problem in problems] + warnings


@isik_check(Check.VALIDATORS)
def every_validator_says_whether_it_can_hold_at_the_column(options, **kwargs):
    """A rule kept only in `full_clean()` is one a direct write ignores - see `db/constraints.py`.

    The validator nobody thought about is the one that errors, rather than the one that quietly
    stays in Python.
    """
    from isik.common.utils.exemptions import enforced
    from isik.django.apps.common.db.constraints import NoDatabaseForm, unclassified_validators

    if not enforced(NoDatabaseForm):  # its rule set to Requirement.OFF asks the same as turning this check off
        return []
    unanswered = unclassified_validators()
    if not unanswered:
        return []

    return [
        Error(
            _("Validators do not say whether they can hold at the column: %(names)s")
            % {"names": ", ".join(unanswered)},
            hint=_(
                "Give the class an `as_condition`, decorate it with `no_database_form(reason)`, or "
                "wrap the instance in `python_only_validator(validator, reason=...)`."
            ),
            id="isik.E001",
        )
    ]


@isik_check(Check.CONCURRENCY_TOKENS)
def every_precondition_view_compares_a_token_it_can_trust(options, **kwargs):
    """A concurrency token that a write can leave unchanged lets a stale write through - see `PreconditionMixin`."""
    if not _views_installed():
        return []
    from isik.django.drf.viewsets.preconditions import precondition_problems

    return [
        Error(
            problem,
            hint=_(
                "See PreconditionMixin (isik.django.drf.viewsets.preconditions) and concurrency tokens "
                "(isik.django.apps.common.db.concurrency)."
            ),
            id="isik.E002",
        )
        for problem in precondition_problems(options[CheckOption.URLCONFS])
    ]


@isik_check(Check.PRECONDITION_OPTIONS)
def every_precondition_view_has_options_that_make_sense(options, **kwargs):
    """An option holding a value it can't take, or two that undo each other - see `PreconditionMixin`."""
    if not _views_installed():
        return []
    from isik.django.drf.viewsets.preconditions import precondition_option_problems, precondition_option_warnings

    hint = _("See PreconditionMixin's options (isik.django.drf.viewsets.preconditions).")
    urlconfs = options[CheckOption.URLCONFS]
    return [Error(problem, hint=hint, id="isik.E003") for problem in precondition_option_problems(urlconfs)] + [
        Warning(warning, hint=hint, id="isik.W001") for warning in precondition_option_warnings(urlconfs)
    ]


@isik_check(Check.RELATION_LOCKS, Tags.database)
def every_concurrency_relation_is_locked(options, databases=None, **kwargs):
    """
    A relation a version covers is safe only while its trigger locks the owner row - see
    `concurrency_relations`. A warning, not an error: `migrate` runs this before it applies the
    migration that installs the trigger, and an error would stop it doing so.
    """
    from isik.django.apps.common.db.models import relation_locks

    warnings = []
    for database in databases or ():
        for owner, relation, proxy, trigger in relation_locks:
            status, _enabled = trigger.get_installation_status(proxy, database=database)
            # A database the router keeps the model off holds no such table to lock.
            if status in ("INSTALLED", "UNALLOWED"):
                continue
            warnings.append(
                Warning(
                    _(
                        "%(owner)s's version covers %(relation)r, but the trigger locking it on %(table)s is "
                        "%(status)s on database %(database)r - a change to it can slip past a write's If-Match."
                    )
                    % {
                        "owner": owner._meta.label,
                        "relation": relation,
                        "table": proxy._meta.db_table,
                        "status": status.lower(),
                        "database": database,
                    },
                    hint=_("Run makemigrations and migrate - the trigger is in the migrations of %(app)s.")
                    % {"app": owner._meta.app_label},
                    id="isik.W002",
                )
            )
    return warnings


@isik_check(Check.VERSION_READS)
def every_guarded_write_has_a_version_to_read(options, **kwargs):
    """A write that asks If-Match, with no read handing out the version, leaves a client nothing to name."""
    if not _views_installed():
        return []
    from isik.django.drf.viewsets.preconditions import precondition_read_problems

    hint = _("See PreconditionMixin's version reads (isik.django.drf.viewsets.preconditions).")
    problems = precondition_read_problems(options[CheckOption.URLCONFS])
    return [Error(problem, hint=hint, id="isik.E004") for problem in problems]


def _named(routed):
    view = routed.view.__qualname__
    return f"{view}.{routed.action}" if routed.action else view


def _uncovered(report):
    """Each uncovered (view, method) once - a router routes one action under several patterns."""
    return list(dict.fromkeys((_named(entry.routed), entry.routed.method) for entry in report if entry.is_uncovered))


@isik_check(Check.IDEMPOTENCY)
def every_routed_write_honors_an_idempotency_key(options, **kwargs):
    """A write a client can't safely retry is one a dropped response makes it do twice - see `IdempotencyMixin`."""
    if not _views_installed():
        return []
    from isik.django.apps.idempotency.coverage import idempotency_coverage

    return [
        Error(
            _("%(view)s answers %(method)s without honoring an Idempotency-Key.") % {"view": view, "method": method},
            hint=_(
                "Add IdempotencyMixin (BaseModelViewSet has it) with the method in idempotent_methods, or exempt "
                "the action in idempotency_exempt_actions."
            ),
            id="isik.E005",
        )
        for view, method in _uncovered(idempotency_coverage(options[CheckOption.URLCONFS]))
    ]


@isik_check(Check.SINGLETONS)
def every_flattened_singleton_is_proven(options, **kwargs):
    """A singleton nothing proves is one a second row makes ambiguous - see `FlattenedSingletonRelatedMixin`."""
    if not _views_installed():
        return []
    from isik.django.drf.serializers.flattened_singleton_related import singleton_related_problems

    hint = _("See FlattenedSingletonRelatedMixin (isik.django.drf.serializers).")
    return [Error(problem, hint=hint, id="isik.E006") for problem in singleton_related_problems()]


@isik_check(Check.PRECONDITIONS)
def every_routed_write_to_one_object_asks_if_match(options, **kwargs):
    """A write to one object without If-Match overwrites what its client never saw - see `PreconditionMixin`."""
    if not _views_installed():
        return []
    from isik.django.drf.coverage import precondition_coverage

    return [
        Error(
            _("%(view)s answers %(method)s without asking If-Match.") % {"view": view, "method": method},
            hint=_(
                "Add PreconditionMixin (BaseModelViewSet has it) with the method in precondition_methods, or "
                "exempt the action in precondition_exempt_actions."
            ),
            id="isik.E007",
        )
        for view, method in _uncovered(precondition_coverage(options[CheckOption.URLCONFS]))
    ]


@isik_check(Check.HISTORY_INDEXES)
def every_event_model_indexes_its_streams(options, **kwargs):
    """A history read one object at a time, without the index for it, slows with the square of its length."""
    if not apps.is_installed("pghistory"):
        return []
    from isik.django.apps.common.db.history import history_index_problems

    hint = _("See object_stream_index() (isik.django.apps.common.db.history).")
    return [Error(problem, hint=hint, id="isik.E008") for problem in history_index_problems(_project_configs(options))]


@isik_check(Check.EXEMPTIONS)
def every_exemption_is_seen(options, **kwargs):
    """An exemption loading the project didn't run is one `manage.py exemptions` can't list."""
    from isik.django.apps.common.exemptions import unimported_project_exemption_types, unseen_project_exemptions

    hint = _("Make it where loading the project runs it - see manage.py exemptions.")
    urlconfs = options[CheckOption.URLCONFS]
    unseen = [
        _("%(file)s:%(line)s makes an exemption through %(name)s that loading the project didn't.")
        % {"file": file, "line": line, "name": name}
        for file, line, name in unseen_project_exemptions(urlconfs)
    ]
    unimported = [
        _("%(file)s:%(line)s declares %(name)s, which loading the project didn't import.")
        % {"file": file, "line": line, "name": name}
        for file, line, name in unimported_project_exemption_types(urlconfs)
    ]
    return [Error(problem, hint=hint, id="isik.E009") for problem in unseen + unimported]


@isik_check(Check.DOCUMENTATION)
def every_field_says_what_it_holds(options, **kwargs):
    """A field without help_text or db_comment leaves its next reader to guess - see `documentation_problems()`."""
    from isik.common.utils.exemptions import Exemption
    from isik.django.apps.common.db.documentation import documentation_problems

    given = options[CheckOption.EXEMPTION_TYPES] or (Exemption,)
    types = [import_string(each) if isinstance(each, str) else each for each in given]
    hint = _("See documentation_problems() (isik.django.apps.common.db.documentation).")
    return [
        Error(problem, hint=hint, id="isik.E010")
        for problem in documentation_problems(
            _project_configs(options),
            exemption_types=types,
            inherited_from_libraries=options[CheckOption.INHERITED_FROM_LIBRARIES],
        )
    ]


@isik_check(Check.NOT_ATOMIC)
def every_view_outside_the_transaction_says_why(options, **kwargs):
    """A view served outside `ATOMIC_REQUESTS` loses every rollback - see `not_atomic()`."""
    if not _views_installed():
        return []
    from isik.django.apps.common.atomic import not_atomic_problems

    hint = _("See not_atomic() (isik.django.apps.common.atomic).")
    return [Error(problem, hint=hint, id="isik.E011") for problem in not_atomic_problems(options[CheckOption.URLCONFS])]


@isik_check(Check.KEPT_FIELD_CONDITIONS)
def no_trigger_condition_compares_a_kept_field(options, **kwargs):
    """A condition comparing version or updated_at sees every change as one to them - see `db/conditions.py`."""
    from isik.django.apps.common.db.conditions import kept_field_condition_problems

    hint = _("See AnyChange and its siblings (isik.django.apps.common.db.conditions), which leave them out.")
    return [
        Error(problem, hint=hint, id="isik.E013")
        for problem in kept_field_condition_problems(_project_configs(options))
    ]
