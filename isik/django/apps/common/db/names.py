"""declared_name_problems() - each name a model leaves Django to generate, by the kind of name it is."""

import copy

from django.db import models

from isik._internal.translation import gettext as _
from isik.django.apps.common.checks import Name
from isik.django.apps.common.db.documentation import _from_a_library, _skipped


def _table_problems(model, names):
    problems = []
    if Name.TABLE in names and "db_table" not in model._meta.original_attrs:
        problems.append(
            _("%(model)s's table is named by Django, %(table)s - declare its Meta.db_table.")
            % {"model": model._meta.label, "table": model._meta.db_table}
        )
    if Name.INDEX in names:
        problems.extend(
            _("%(model)s's index %(name)s is named by Django - give it a name.")
            % {"model": model._meta.label, "name": index.name}
            for index in model._meta.indexes
            if index.fields and index.name == _generated_index_name(model, index)
        )
    return problems


def _generated_index_name(model, index):
    unnamed = copy.copy(index)
    unnamed.set_name_with_model(model)
    return unnamed.name


def _model_verbose_name_problems(model):
    return [
        _("%(model)s's %(option)s is Django's, %(value)r - declare its Meta.%(option)s.")
        % {"model": model._meta.label, "option": option, "value": str(getattr(model._meta, option))}
        for option in ("verbose_name", "verbose_name_plural")
        if option not in model._meta.original_attrs
    ]


def _relation_problems(model, field, names):
    where = {"model": model._meta.label, "field": field.name}
    remote = field.remote_field
    problems = []
    if Name.RELATED_NAME in names and remote.related_name is None:
        problems.append(
            _("%(model)s.%(field)s's related_name is Django's, %(value)s - declare it, \"+\" for no reverse accessor.")
            % {**where, "value": remote.get_accessor_name()}
        )
    if Name.RELATED_QUERY_NAME in names and remote.related_query_name is None and not remote.hidden:
        problems.append(
            _("%(model)s.%(field)s's related_query_name is Django's, %(value)s - declare it.")
            % {**where, "value": field.related_query_name()}
        )
    if field.many_to_many and remote.through._meta.auto_created:
        if Name.THROUGH_TABLE in names and field.db_table is None:
            problems.append(
                _("%(model)s.%(field)s's join table is named by Django, %(table)s - declare its db_table.")
                % {**where, "table": remote.through._meta.db_table}
            )
        if Name.THROUGH_MODEL in names:
            problems.append(
                _("%(model)s.%(field)s goes through %(through)s, a model Django makes - declare its through model.")
                % {**where, "through": remote.through.__name__}
            )
    return problems


def _field_problems(model, field, names):
    problems = []
    if isinstance(field, models.ForeignObject) or field.many_to_many:
        problems.extend(_relation_problems(model, field, names))
    if Name.VERBOSE_NAME in names and field._verbose_name is None:
        problems.append(
            _("%(model)s.%(field)s's verbose_name is Django's, %(value)r - declare it.")
            % {"model": model._meta.label, "field": field.name, "value": str(field.verbose_name)}
        )
    return problems


def declared_name_problems(app_configs, names=frozenset(Name), inherited_from_libraries=True):
    """
    Each name of `names` - `Name` members - the models of `app_configs` leave Django to generate, one sentence
    each, the name Django gave it included:

    - `Name.TABLE` - a managed model's table, `<app_label>_<model>` unless `Meta.db_table` names it.
    - `Name.INDEX` - each `Meta.indexes` entry's name.
    - `Name.RELATED_NAME` - each foreign key's, one-to-one's and many-to-many's `related_name`; `"+"` declares
      there's no reverse accessor.
    - `Name.RELATED_QUERY_NAME` - each one's `related_query_name`, where it has a reverse accessor.
    - `Name.THROUGH_TABLE` - the join table of each many-to-many going through a model Django makes, named by
      its `db_table`.
    - `Name.THROUGH_MODEL` - the model each many-to-many goes through, declared as its `through`.
    - `Name.VERBOSE_NAME` - each model's `verbose_name` and `verbose_name_plural`, and each field's `verbose_name`.

    Tables and indexes are a managed model's, proxies left out. Every other name is held of the models a project
    writes, and of the fields it declares: the models Django, isik or pghistory generate are left out, as are the
    fields Django adds itself. With `inherited_from_libraries=False`, so are the fields a model inherits unchanged
    from a library's abstract base - the project declares those names through `redeclare()`.

    The system check `isik.E019`, off unless `ISIK['DJANGO']['CHECKS']` turns it on, reports them.
    """
    names = frozenset(names)
    problems = []
    for config in app_configs:
        for model in config.get_models():
            if model._meta.managed and not model._meta.proxy:
                problems.extend(_table_problems(model, names))
            if _skipped(model):
                continue
            if Name.VERBOSE_NAME in names:
                problems.extend(_model_verbose_name_problems(model))
            for field in [*model._meta.local_fields, *model._meta.local_many_to_many]:
                if field.auto_created or (not inherited_from_libraries and _from_a_library(model, field)):
                    continue
                problems.extend(_field_problems(model, field, names))
    return problems
