import uuid

import pghistory
import pytest
from django.db import connection
from pghistory.models import Context

from isik.django.apps.common.db import AddContextColumn, context_rows
from tests.testapp.models import Widget, WidgetEvent


def columns():
    with connection.cursor() as cursor:
        described = connection.introspection.get_table_description(cursor, Context._meta.db_table)
    return {column.name for column in described}


def indexes():
    with connection.cursor() as cursor:
        return set(connection.introspection.get_constraints(cursor, Context._meta.db_table))


def apply(*operations, backwards=False):
    with connection.schema_editor() as editor:
        for operation in operations:
            if backwards:
                operation.database_backwards("testapp", editor, None, None)
            else:
                operation.database_forwards("testapp", editor, None, None)


def written(**metadata):
    """A widget made inside a context holding `metadata` - its event's context row."""
    with pghistory.context(**metadata):
        widget = Widget.objects.create(name="w")
    return WidgetEvent.objects.get(pgh_obj_id=widget.pk).pgh_context


@pytest.mark.django_db
class TestAColumn:
    def test_a_uuid_column_is_generated_from_its_key(self):
        apply(AddContextColumn("correlation_id", "uuid"))
        correlation = uuid.uuid4()

        mine = written(correlation_id=str(correlation))
        written(correlation_id=str(uuid.uuid4()))

        assert list(context_rows(correlation_id=correlation)) == [mine]
        assert list(WidgetEvent.objects.filter(pgh_context__in=context_rows(correlation_id=correlation))) == list(
            WidgetEvent.objects.filter(pgh_context=mine)
        )

    def test_a_value_that_isnt_a_uuid_is_held_as_null_and_the_write_goes_through(self):
        apply(AddContextColumn("request_id", "uuid"))

        context = written(request_id="not a uuid")

        assert context.metadata["request_id"] == "not a uuid"
        assert list(context_rows(request_id="0123456789abcdef0123456789abcdef")) == []
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT request_id FROM {Context._meta.db_table} WHERE id = %s", [context.pk])
            assert cursor.fetchone() == (None,)

    def test_a_text_column_reading_another_key(self):
        apply(AddContextColumn("tenant", "text", key="schema"))

        context = written(schema="acme")

        assert list(context_rows(tenant="acme")) == [context]

    def test_indexed_unless_told_not_to(self):
        apply(AddContextColumn("request_id", "uuid"), AddContextColumn("trace_id", "uuid", index=False))

        assert {"request_id", "trace_id"} <= columns()
        assert "pghistory_context_request_id" in indexes()
        assert not any("trace_id" in name for name in indexes())

    def test_reversed_the_column_and_its_index_go(self):
        operation = AddContextColumn("request_id", "uuid")
        apply(operation)

        apply(operation, backwards=True)

        assert "request_id" not in columns()
        assert "pghistory_context_request_id" not in indexes()


class TestTheOperation:
    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (
                ("Request", "uuid"),
                "A context column's name and key are lowercase identifiers - not 'Request' and 'Request'.",
            ),
            (
                ("request_id", "uuid", "bad key"),
                "A context column's name and key are lowercase identifiers - not 'request_id' and 'bad key'.",
            ),
            (("metadata", "text"), "metadata is a column of pghistory's context table already."),
            (("request_id", "jsonb"), "A context column is uuid or text, not 'jsonb'."),
        ],
    )
    def test_refused(self, args, message):
        with pytest.raises(ValueError) as raised:
            AddContextColumn(*args)

        assert str(raised.value) == message

    def test_written_into_a_migration_as_given(self):
        assert AddContextColumn("request_id", "uuid").deconstruct() == (
            "AddContextColumn",
            [],
            {"name": "request_id", "column_type": "uuid"},
        )
        assert AddContextColumn("tenant", "text", key="schema", index=False).deconstruct() == (
            "AddContextColumn",
            [],
            {"name": "tenant", "column_type": "text", "key": "schema", "index": False},
        )

    def test_described(self):
        operation = AddContextColumn("tenant", "text", key="schema")

        assert operation.describe() == "Add text column tenant to pghistory's context table, from its schema key"
        assert operation.migration_name_fragment == "context_tenant"
        assert operation.reversible and operation.reduces_to_sql

    def test_the_migration_state_is_pghistorys_own(self):
        state = object()

        assert AddContextColumn("request_id", "uuid").state_forwards("testapp", state) is None


class TestReading:
    def test_one_column_at_a_time(self):
        with pytest.raises(TypeError, match=r"^context_rows\(\) takes one column and its value\.$"):
            context_rows(request_id=1, trace_id=2)
        with pytest.raises(TypeError):
            context_rows()

    @pytest.mark.parametrize("name", ["metadata", "Request", "id"])
    def test_a_column_it_doesnt_add(self, name):
        with pytest.raises(ValueError, match=f"^{name!r} isn't a column AddContextColumn adds.$"):
            context_rows(**{name: 1})
