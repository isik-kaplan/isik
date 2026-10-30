"""
Bringing a database migrated before isik 0.43.0 onto the names 0.43.0 gave isik's tables.

0.43.0 started each of isik's apps' migrations again at `0001_initial` - under the name the old first migration
had, so a database that applied that one takes the new one as applied too, and keeps the old tables. Each app's
`0002_isik_names` is a `RenamedTables`: nothing on a database migrated afresh, and on one migrated before, the
old tables and their constraints and indexes renamed, the tables it never had made, and the app's triggers
installed again. Postgres's own names for a table's primary key and the like keep the old table's, as they do
whenever Django renames a table.
"""

from django.conf import settings
from django.db.migrations.operations.base import Operation


class RenamedTables(Operation):
    """
    A migration operation for an app whose migrations started again: on a database holding the tables `tables`
    names - {model name: the table it was} - renames each to its model's table now, each of `names` ({old: new},
    constraints and indexes), and each `unique_together`'s generated name; makes each of the app's tables that
    database never had; and installs the app's triggers again, since a trigger's function names the tables it
    reads by name. A database without any of the old tables is left as it is. A model the project swapped out has
    no table of isik's to rename.

    It reads the migration state's models without rendering them - a swapped model keys a project's app the state
    may not hold yet - and renders them only to make a table a database never had.
    """

    reduces_to_sql = False

    def __init__(self, tables, names):
        self.tables = tables
        self.names = names

    def deconstruct(self):
        return type(self).__name__, [self.tables, self.names], {}

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        bring_over(app_label, from_state, self.tables, self.names, schema_editor)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def describe(self):
        return "Bring tables migrated before isik 0.43.0 onto their names"


def bring_over(app_label, state, tables, names, schema_editor):
    """`RenamedTables`' work, over `app_label`'s models in `state`."""
    models = {name: state.models[app_label, name.lower()] for name in tables}
    models = {name: model for name, model in models.items() if not _swapped(app_label, model)}
    found = {name: tables[name] for name in models if _exists(schema_editor, tables[name])}
    if not found:
        return
    quote = schema_editor.quote_name
    for name, old in found.items():
        model = models[name]
        new = model.options["db_table"]
        schema_editor.execute(f"ALTER TABLE {quote(old)} RENAME TO {quote(new)}")
        for fields in model.options.get("unique_together", ()):
            columns = [model.fields[field].db_column or field for field in fields]
            _rename(
                schema_editor,
                schema_editor._create_index_name(old, columns, suffix="_uniq"),
                schema_editor._create_index_name(new, columns, suffix="_uniq"),
            )
    for name, model in models.items():
        if not _exists(schema_editor, model.options["db_table"]):
            # A table of a version after the one that database was migrated by.
            schema_editor.create_model(state.apps.get_model(app_label, name))
    for old, new in names.items():
        _rename(schema_editor, old, new)
    for model in models.values():
        for trigger in model.options.get("triggers", ()):
            schema_editor.execute(trigger.install_sql, params=None)


def _swapped(app_label, model):
    swappable = model.options.get("swappable")
    if swappable is None:
        return False
    return getattr(settings, swappable).lower() != f"{app_label}.{model.name}".lower()


def _exists(schema_editor, table):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [schema_editor.quote_name(table)])
        return cursor.fetchone()[0] is not None


def _rename(schema_editor, old, new):
    """Renames the index called `old` - and the UNIQUE constraint it backs, which Postgres renames with it."""
    quote = schema_editor.quote_name
    schema_editor.execute(f"ALTER INDEX IF EXISTS {quote(old)} RENAME TO {quote(new)}")
