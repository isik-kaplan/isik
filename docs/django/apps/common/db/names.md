# names

`declared_name_problems(app_configs, names=frozenset(Name), inherited_from_libraries=True)`
(`isik.django.apps.common.db.names`) lists each name a project's models leave Django to generate, by the
kind of name it is. The system check `isik.E019` (`Check.DECLARED_NAMES`) reports them once a project turns it
on, and holds every model to the names it declares, the next one added included:

```python
from isik.django.apps.common.checks import Check, CheckOption, Name

ISIK = {"DJANGO": {"CHECKS": {
    Check.DECLARED_NAMES: {
        CheckOption.ENABLED: True,
        CheckOption.NAMES: {Name.TABLE, Name.INDEX, Name.RELATED_NAME, Name.RELATED_QUERY_NAME},
    },
}}}
```

`CheckOption.NAMES` is a set of `Name` members, every one by default:

| `Name` | what the model declares | Django's name otherwise |
|---|---|---|
| `TABLE` | `Meta.db_table` | `<app_label>_<model>` |
| `INDEX` | a `name` for each `Meta.indexes` entry | a hash of its table and fields |
| `RELATED_NAME` | each foreign key's, one-to-one's and many-to-many's `related_name`, `"+"` for no reverse accessor | `<model>_set`, or `<model>` for a one-to-one |
| `RELATED_QUERY_NAME` | each one's `related_query_name`, where it has a reverse accessor | its `related_name`, else `<model>` |
| `THROUGH_TABLE` | the `db_table` of each many-to-many's join table | `<model's table>_<field>` |
| `THROUGH_MODEL` | each many-to-many's `through`, a model of the project's | a model Django makes, `<Model>_<field>` |
| `VERBOSE_NAME` | each model's `Meta.verbose_name` and `Meta.verbose_name_plural`, and each field's `verbose_name` | the model's or field's name in words, and the singular with an `s` |

`Meta.default_related_name` declares the `related_name` of every relation of its model.

Tables and indexes are held of every managed model of the project's apps (`CheckOption.APPS`). Every other
name is held of the models the project writes and the fields it declares: the models Django, isik or pghistory
generate - a many-to-many's join model, `votes()` and `tags()`'s, an event model - and the fields Django adds
itself are the generator's to name. A field a model inherits from a library's abstract base is held too, and
the project names it with `redeclare()` ([fields](fields.md)) -
`CheckOption.INHERITED_FROM_LIBRARIES: False` holds only the fields it declares itself. isik's own abstract bases
declare every name, so a model on them is held only to its own.
