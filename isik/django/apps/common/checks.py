"""isik's own system checks."""

from django.core.checks import Error, register

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
