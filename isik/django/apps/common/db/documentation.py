"""documentation_problems() - which fields don't say what they hold."""

import inspect

from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption
from isik.django.apps.common.project import is_library_path


def _skipped(model):
    """Models whose fields were written elsewhere: proxies, auto-created ones, and generated ones."""
    from pghistory.models import Event

    return (
        model._meta.proxy
        or model._meta.auto_created
        or getattr(model, "isik_generated", None) is not None
        or issubclass(model, Event)
    )


def _from_a_library(model, field):
    """
    Whether `field` is one `model` inherits, unchanged, from an abstract base a library declares -
    `AbstractUser`'s `username`, a mixin's - rather than one of the project's own, or one it declares
    again. Abstract inheritance copies a field with its `creation_counter`; a field declared again gets
    a new one. The bases are read from the most basic up, so the one found is the one that declared
    it - not a project's abstract base between it and the model, which holds a copy too.
    """
    for base in reversed(model.__mro__[1:]):
        meta = getattr(base, "_meta", None)
        if meta is None or not meta.abstract:
            continue
        declared = {each.name: each for each in [*meta.local_fields, *meta.local_many_to_many]}
        inherited = declared.get(field.name)
        if inherited is not None and inherited.creation_counter == field.creation_counter:
            return is_library_path(inspect.getfile(base))
    return False


def _says(value):
    # An exemption is a reason not to say it, which says nothing about the field - whatever it shows as.
    return not isinstance(value, Exemption) and bool(str(value or "").strip())


def documentation_problems(app_configs, inherited_from_libraries=True):
    """
    Each field of the models in `app_configs` that doesn't say what it holds, one sentence each: no
    `help_text` (what the API schema and the admin show), or - for a field with a column of a managed
    model - no `db_comment` (what someone reading the database sees). An exemption given as either
    answers neither: there is no way out per field. Fields Django adds itself (an implicit `id`, a
    parent link) are left out, as are proxies and the models isik or pghistory generate. With
    `inherited_from_libraries=False`, so are the fields a model inherits unchanged from a library's
    abstract base - its description is the library's to write, or the project's, through `redeclare()`.
    The system check `isik.E010` reports
    them.
    """
    problems = []
    for config in app_configs:
        for model in config.get_models():
            if _skipped(model):
                continue
            for field in [*model._meta.local_fields, *model._meta.local_many_to_many]:
                if field.auto_created or (not inherited_from_libraries and _from_a_library(model, field)):
                    continue
                missing = []
                if not _says(field.help_text):
                    missing.append("help_text")
                has_column = field.concrete and not field.many_to_many and model._meta.managed
                if has_column and not _says(field.db_comment):
                    missing.append("db_comment")
                if missing:
                    problems.append(
                        _("%(model)s.%(field)s has no %(missing)s - say what it holds.")
                        % {"model": model._meta.label, "field": field.name, "missing": " or ".join(missing)}
                    )
    return problems
