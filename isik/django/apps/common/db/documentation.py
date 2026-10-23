"""documentation_problems() - which fields don't say what they hold."""

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption


def _skipped(model):
    """Models whose fields were written elsewhere: proxies, auto-created ones, and generated ones."""
    from pghistory.models import Event

    return (
        model._meta.proxy
        or model._meta.auto_created
        or getattr(model, "isik_generated", None) is not None
        or issubclass(model, Event)
    )


def _answered(value, exemption_types):
    if isinstance(value, Exemption):
        return isinstance(value, exemption_types)
    return bool(str(value or "").strip())


def documentation_problems(app_configs, exemption_types=(Exemption,)):
    """
    Each field of the models in `app_configs` that doesn't say what it holds, one sentence each: no
    `help_text` (what the API schema and the admin show), or - for a field with a column of a managed
    model - no `db_comment` (what someone reading the database sees). An exemption of one of
    `exemption_types` given as either answers it. Fields Django adds itself (an implicit `id`, a
    parent link) are left out, as are proxies and the models isik or pghistory generate. The system
    check `isik.E010` reports them.
    """
    exemption_types = tuple(exemption_types)
    problems = []
    for config in app_configs:
        for model in config.get_models():
            if _skipped(model):
                continue
            for field in [*model._meta.local_fields, *model._meta.local_many_to_many]:
                if field.auto_created:
                    continue
                missing = []
                if not _answered(field.help_text, exemption_types):
                    missing.append("help_text")
                has_column = field.concrete and not field.many_to_many and model._meta.managed
                if has_column and not _answered(field.db_comment, exemption_types):
                    missing.append("db_comment")
                if missing:
                    problems.append(
                        _("%(model)s.%(field)s has no %(missing)s - say what it holds, or give an exemption why not.")
                        % {"model": model._meta.label, "field": field.name, "missing": " or ".join(missing)}
                    )
    return problems
