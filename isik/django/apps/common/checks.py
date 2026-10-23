"""isik's own system checks."""

from django.apps import apps
from django.core.checks import Error, Tags, Warning, register

from isik._internal.translation import gettext as _


@register()
def every_validator_says_whether_it_can_hold_at_the_column(app_configs, **kwargs):
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


@register()
def every_precondition_view_compares_a_token_it_can_trust(app_configs, **kwargs):
    """A concurrency token that a write can leave unchanged lets a stale write through - see `PreconditionMixin`."""
    if not apps.is_installed("rest_framework"):
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
        for problem in precondition_problems()
    ]


@register()
def every_precondition_view_has_options_that_make_sense(app_configs, **kwargs):
    """An option holding a value it can't take, or two that undo each other - see `PreconditionMixin`."""
    if not apps.is_installed("rest_framework"):
        return []
    from isik.django.drf.viewsets.preconditions import precondition_option_problems, precondition_option_warnings

    hint = _("See PreconditionMixin's options (isik.django.drf.viewsets.preconditions).")
    return [Error(problem, hint=hint, id="isik.E003") for problem in precondition_option_problems()] + [
        Warning(warning, hint=hint, id="isik.W001") for warning in precondition_option_warnings()
    ]


@register(Tags.database)
def every_concurrency_relation_is_locked(app_configs, databases=None, **kwargs):
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


@register()
def every_guarded_write_has_a_version_to_read(app_configs, **kwargs):
    """A write that asks If-Match, with no read handing out the version, leaves a client nothing to name."""
    if not apps.is_installed("rest_framework"):
        return []
    from isik.django.drf.viewsets.preconditions import precondition_read_problems

    hint = _("See PreconditionMixin's version reads (isik.django.drf.viewsets.preconditions).")
    return [Error(problem, hint=hint, id="isik.E004") for problem in precondition_read_problems()]
