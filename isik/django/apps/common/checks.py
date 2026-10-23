"""
isik's own system checks - each on by default, and each tuned or turned off by name in `ISIK_CHECKS`:

    ISIK_CHECKS = {
        "documentation": {"apps": ["billing", "accounts"]},
        "idempotency": {"urlconfs": ["config.urls.api"]},
        "not_atomic": {"severity": "warning"},
        "history_indexes": {"enabled": False},
    }

Every check takes `enabled` (default True) and `severity` (`"error"` or `"warning"`, default the
check's own). A check over routed views takes `urlconfs` - every urlconf the project serves by
default (`project_urlconfs()`), and a warning, `isik.W003`, names each served one it is told to skip.
A check over models takes `apps`, app labels - the project's own apps by default
(`project_app_configs()`). `documentation` also takes `exemption_types`, the `Exemption` types (or
their dotted paths) that answer for a field. Anything else in `ISIK_CHECKS` is an error, `isik.E012`.

| name                 | id                     | what it refuses                                              |
|----------------------|------------------------|--------------------------------------------------------------|
| validators           | isik.E001              | a validator that doesn't say whether it holds at the column  |
| concurrency_tokens   | isik.E002              | a precondition view comparing a token a write can keep       |
| precondition_options | isik.E003, isik.W001   | precondition options that are invalid or undo each other    |
| relation_locks       | isik.W002              | a covered relation whose lock trigger isn't installed        |
| version_reads        | isik.E004              | a write asking If-Match with no read handing out the version |
| idempotency          | isik.E005              | a routed write that honors no Idempotency-Key                |
| singletons           | isik.E006              | a flattened singleton no unique constraint proves            |
| preconditions        | isik.E007              | a routed write to one object that asks no If-Match           |
| history_indexes      | isik.E008              | an event model without its (pgh_obj, -pgh_id) index          |
| exemptions           | isik.E009              | an exemption loading the project didn't see                  |
| documentation        | isik.E010              | a field without help_text or db_comment                      |
| not_atomic           | isik.E011              | a view outside ATOMIC_REQUESTS that doesn't say why          |
"""

import functools

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _


SEVERITIES = {"error": Error, "warning": Warning}

URLCONFS = "urlconfs"
APPS = "apps"
EXEMPTION_TYPES = "exemption_types"

# {name: the options it takes beyond enabled and severity}
CHECKS = {
    "validators": (),
    "concurrency_tokens": (URLCONFS,),
    "precondition_options": (URLCONFS,),
    "relation_locks": (),
    "version_reads": (URLCONFS,),
    "idempotency": (URLCONFS,),
    "singletons": (),
    "preconditions": (URLCONFS,),
    "history_indexes": (APPS,),
    "exemptions": (URLCONFS,),
    "documentation": (APPS, EXEMPTION_TYPES),
    "not_atomic": (URLCONFS,),
}


def _configured():
    return getattr(settings, "ISIK_CHECKS", None) or {}


def check_options(name):
    """The options check `name` runs with: its `ISIK_CHECKS` entry over the defaults."""
    defaults = {"enabled": True, "severity": None, URLCONFS: None, APPS: None, EXEMPTION_TYPES: None}
    given = _configured().get(name)
    return {**defaults, **given} if isinstance(given, dict) else defaults


def isik_check(name, *tags):
    """
    Registers `function(options)` as check `name`: skipped when `ISIK_CHECKS` turns it off, and its
    messages given the severity `ISIK_CHECKS` sets, keeping their ids.
    """

    def decorate(function):
        @register(*tags)
        @functools.wraps(function)
        def check(app_configs, **kwargs):
            return run_check(name, function, **kwargs)

        return check

    return decorate


def run_check(name, function, **kwargs):
    """Runs `function(options, **kwargs)` as check `name` - see `isik_check()`."""
    options = check_options(name)
    if not options["enabled"]:
        return []
    messages = function(options, **kwargs)
    severity = SEVERITIES.get(options["severity"])
    if severity is None:
        return messages
    return [severity(message.msg, hint=message.hint, obj=message.obj, id=message.id) for message in messages]


def _project_configs(options):
    from isik.django.apps.common.project import project_app_configs

    return project_app_configs(options[APPS])


def _views_installed():
    return apps.is_installed("rest_framework")


@register()
def isik_checks_are_configured(app_configs, **kwargs):
    """`ISIK_CHECKS` names only checks and options there are - and says when it leaves served urlconfs unchecked."""
    from isik.django.apps.common.urlconfs import project_urlconfs

    configured = _configured()
    if not isinstance(configured, dict):
        return [Error(_("ISIK_CHECKS is %(value)r, not a dict.") % {"value": configured}, id="isik.E012")]
    messages = []
    hint = _("See isik.django.apps.common.checks.")
    for name, options in configured.items():
        if name not in CHECKS:
            messages.append(
                Error(
                    _("ISIK_CHECKS names %(name)r, which isn't one of isik's checks: %(names)s.")
                    % {"name": name, "names": ", ".join(CHECKS)},
                    hint=hint,
                    id="isik.E012",
                )
            )
            continue
        if not isinstance(options, dict):
            messages.append(
                Error(
                    _("ISIK_CHECKS[%(name)r] is %(value)r, not a dict.") % {"name": name, "value": options},
                    hint=hint,
                    id="isik.E012",
                )
            )
            continue
        takes = ("enabled", "severity", *CHECKS[name])
        unknown = [option for option in options if option not in takes]
        if unknown:
            messages.append(
                Error(
                    _("ISIK_CHECKS[%(name)r] has %(unknown)s - it takes %(takes)s.")
                    % {"name": name, "unknown": ", ".join(map(repr, unknown)), "takes": ", ".join(takes)},
                    hint=hint,
                    id="isik.E012",
                )
            )
        if options.get("severity") not in (None, *SEVERITIES):
            messages.append(
                Error(
                    _("ISIK_CHECKS[%(name)r]'s severity is %(value)r - it takes %(choices)s.")
                    % {"name": name, "value": options["severity"], "choices": ", ".join(SEVERITIES)},
                    hint=hint,
                    id="isik.E012",
                )
            )
        if options.get(URLCONFS) is not None and options.get("enabled", True):
            checked = project_urlconfs(options[URLCONFS])
            skipped = [urlconf for urlconf in project_urlconfs() if urlconf not in checked]
            if skipped:
                messages.append(
                    Warning(
                        _("ISIK_CHECKS[%(name)r] leaves served urlconfs unchecked: %(urlconfs)s.")
                        % {"name": name, "urlconfs": ", ".join(skipped)},
                        hint=_("Leave out urlconfs to check every urlconf the project serves."),
                        id="isik.W003",
                    )
                )
    return messages


@isik_check("validators")
def every_validator_says_whether_it_can_hold_at_the_column(options, **kwargs):
    """A rule kept only in `full_clean()` is one a direct write ignores - see `db/constraints.py`.

    The validator nobody thought about is the one that errors, rather than the one that quietly
    stays in Python.
    """
    from isik.django.apps.common.db.constraints import unclassified_validators

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


@isik_check("concurrency_tokens")
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
        for problem in precondition_problems(options[URLCONFS])
    ]


@isik_check("precondition_options")
def every_precondition_view_has_options_that_make_sense(options, **kwargs):
    """An option holding a value it can't take, or two that undo each other - see `PreconditionMixin`."""
    if not _views_installed():
        return []
    from isik.django.drf.viewsets.preconditions import precondition_option_problems, precondition_option_warnings

    hint = _("See PreconditionMixin's options (isik.django.drf.viewsets.preconditions).")
    urlconfs = options[URLCONFS]
    return [Error(problem, hint=hint, id="isik.E003") for problem in precondition_option_problems(urlconfs)] + [
        Warning(warning, hint=hint, id="isik.W001") for warning in precondition_option_warnings(urlconfs)
    ]


@isik_check("relation_locks", Tags.database)
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


@isik_check("version_reads")
def every_guarded_write_has_a_version_to_read(options, **kwargs):
    """A write that asks If-Match, with no read handing out the version, leaves a client nothing to name."""
    if not _views_installed():
        return []
    from isik.django.drf.viewsets.preconditions import precondition_read_problems

    hint = _("See PreconditionMixin's version reads (isik.django.drf.viewsets.preconditions).")
    return [Error(problem, hint=hint, id="isik.E004") for problem in precondition_read_problems(options[URLCONFS])]


def _named(routed):
    view = routed.view.__qualname__
    return f"{view}.{routed.action}" if routed.action else view


def _uncovered(report):
    """Each uncovered (view, method) once - a router routes one action under several patterns."""
    return list(dict.fromkeys((_named(entry.routed), entry.routed.method) for entry in report if entry.is_uncovered))


@isik_check("idempotency")
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
        for view, method in _uncovered(idempotency_coverage(options[URLCONFS]))
    ]


@isik_check("singletons")
def every_flattened_singleton_is_proven(options, **kwargs):
    """A singleton nothing proves is one a second row makes ambiguous - see `FlattenedSingletonRelatedMixin`."""
    if not _views_installed():
        return []
    from isik.django.drf.serializers.flattened_singleton_related import singleton_related_problems

    hint = _("See FlattenedSingletonRelatedMixin (isik.django.drf.serializers).")
    return [Error(problem, hint=hint, id="isik.E006") for problem in singleton_related_problems()]


@isik_check("preconditions")
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
        for view, method in _uncovered(precondition_coverage(options[URLCONFS]))
    ]


@isik_check("history_indexes")
def every_event_model_indexes_its_streams(options, **kwargs):
    """A history read one object at a time, without the index for it, slows with the square of its length."""
    if not apps.is_installed("pghistory"):
        return []
    from isik.django.apps.common.db.history import history_index_problems

    hint = _("See object_stream_index() (isik.django.apps.common.db.history).")
    return [Error(problem, hint=hint, id="isik.E008") for problem in history_index_problems(_project_configs(options))]


@isik_check("exemptions")
def every_exemption_is_seen(options, **kwargs):
    """An exemption loading the project didn't run is one `manage.py exemptions` can't list."""
    from isik.django.apps.common.exemptions import unimported_project_exemption_types, unseen_project_exemptions

    hint = _("Make it where loading the project runs it - see manage.py exemptions.")
    urlconfs = options[URLCONFS]
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


@isik_check("documentation")
def every_field_says_what_it_holds(options, **kwargs):
    """A field without help_text or db_comment leaves its next reader to guess - see `documentation_problems()`."""
    from isik.common.utils.exemptions import Exemption
    from isik.django.apps.common.db.documentation import documentation_problems

    types = [
        import_string(each) if isinstance(each, str) else each for each in options[EXEMPTION_TYPES] or (Exemption,)
    ]
    hint = _("See documentation_problems() (isik.django.apps.common.db.documentation).")
    return [
        Error(problem, hint=hint, id="isik.E010")
        for problem in documentation_problems(_project_configs(options), exemption_types=types)
    ]


@isik_check("not_atomic")
def every_view_outside_the_transaction_says_why(options, **kwargs):
    """A view served outside `ATOMIC_REQUESTS` loses every rollback - see `not_atomic()`."""
    if not _views_installed():
        return []
    from isik.django.apps.common.atomic import not_atomic_problems

    hint = _("See not_atomic() (isik.django.apps.common.atomic).")
    return [Error(problem, hint=hint, id="isik.E011") for problem in not_atomic_problems(options[URLCONFS])]
