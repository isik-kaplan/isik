"""A database migrated before 0.43.0 brought onto 0.43.0's names - RenamedTables, in each app's 0002."""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test.utils import CaptureQueriesContext

from isik.django.apps.common.db.renaming import RenamedTables


pytestmark = pytest.mark.django_db


def migration(app_label):
    """The app's 0002_isik_names operation, and the state it runs on."""
    loader = MigrationLoader(connection)
    (operation,) = loader.get_migration(app_label, "0002_isik_names").operations
    return operation, loader.project_state((app_label, "0001_initial"))


def run(app_label, operation=None):
    found, state = migration(app_label)
    with connection.schema_editor() as editor:
        (operation or found).database_forwards(app_label, editor, state, state)
    return state


def execute(*statements):
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def fetch(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [row[0] for row in cursor.fetchall()]


def tables(*names):
    return fetch("SELECT tablename FROM pg_tables WHERE tablename = ANY(%s) ORDER BY 1", [list(names)])


def constraints(*names):
    return fetch("SELECT conname FROM pg_constraint WHERE conname = ANY(%s) ORDER BY 1", [list(names)])


def indexes(*names):
    return fetch("SELECT indexname FROM pg_indexes WHERE indexname = ANY(%s) ORDER BY 1", [list(names)])


def triggers(table):
    return fetch("SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal", [table])


def test_it_undoes_nothing_backwards():
    operation, state = migration("idempotency_by_reference")
    with connection.schema_editor() as editor, CaptureQueriesContext(connection) as queries:
        operation.database_backwards("idempotency_by_reference", editor, state, state)

    assert (operation.reversible, operation.reduces_to_sql, len(queries)) == (True, False, 0)


def test_it_writes_itself_into_a_migration_as_it_was_made():
    operation = RenamedTables({"Notice": "old_notice"}, {"old": "new"})
    state = ProjectState()

    operation.state_forwards("isik_notifications", state)

    assert operation.deconstruct() == ("RenamedTables", [{"Notice": "old_notice"}, {"old": "new"}], {})
    assert operation.describe() == "Bring tables migrated before isik 0.43.0 onto their names"
    assert state.models == {}


def test_a_database_migrated_afresh_is_left_as_it_is():
    operation, state = migration("isik_notifications")
    with connection.schema_editor() as editor, CaptureQueriesContext(connection) as queries:
        operation.database_forwards("isik_notifications", editor, state, state)

    # Never rendered: a project swapping the ledger keys its own app, which the state may not hold yet.
    assert "apps" not in state.__dict__

    # One look for each old table, and nothing after.
    assert sorted(query["sql"] for query in queries) == sorted(
        f"""SELECT to_regclass('"isik_notifications_{table}"')"""
        for table in (
            "noticekindpolicy",
            "notice",
            "fanoutshard",
            "delivery",
            "webhookdelivery",
            "emaildelivery",
            "inboxdelivery",
            "pushdelivery",
            "deliveryattempt",
        )
    )
    assert tables("isik_notice", "isik_delivery") == ["isik_delivery", "isik_notice"]


def test_an_old_claim_table_takes_its_new_name_and_keeps_its_rows():
    user = get_user_model().objects.create(email="old@example.com")
    key = uuid.uuid4()
    execute(
        f"INSERT INTO isik_idempotency_claim (claimed_by_id, key, fingerprint, status, claimed_at) "
        f"VALUES ({user.pk}, '{key}', 'f', 201, now())",
        "ALTER TABLE isik_idempotency_claim RENAME TO idempotency_by_reference_idempotencyclaim",
        "ALTER TABLE idempotency_by_reference_idempotencyclaim RENAME CONSTRAINT isik_idempotency_claim_key "
        "TO idempotency_by_reference_idempotencyclaim_key",
    )

    run("idempotency_by_reference")

    assert tables("isik_idempotency_claim", "idempotency_by_reference_idempotencyclaim") == ["isik_idempotency_claim"]
    assert constraints("isik_idempotency_claim_key", "idempotency_by_reference_idempotencyclaim_key") == [
        "isik_idempotency_claim_key"
    ]
    assert fetch("SELECT key::text FROM isik_idempotency_claim") == [str(key)]


def test_an_old_ledger_takes_its_index_names_and_the_tables_it_never_had():
    execute(
        "ALTER TABLE isik_delivery RENAME TO isik_notifications_delivery",
        "ALTER INDEX isik_delivery_due RENAME TO delivery_due_c7f4e1c9",
        "ALTER TABLE isik_notifications_delivery RENAME CONSTRAINT isik_delivery_once "
        "TO isik_notifications_delivery_once",
        "DROP TABLE isik_push_delivery",
    )

    run("isik_notifications")

    assert tables("isik_delivery", "isik_notifications_delivery", "isik_push_delivery") == [
        "isik_delivery",
        "isik_push_delivery",
    ]
    assert indexes("isik_delivery_due", "delivery_due_c7f4e1c9") == ["isik_delivery_due"]
    assert constraints("isik_delivery_once", "isik_notifications_delivery_once") == ["isik_delivery_once"]


def test_an_old_audience_takes_its_generated_names_and_its_triggers_again():
    device_unique = "isik_device_platform_token_450f0b44_uniq"
    (guard,) = [name for name in triggers("isik_subscription") if name.startswith("pgtrigger_guard_variant")]
    execute(
        "ALTER TABLE isik_device RENAME TO isik_notifications_audience_device",
        f"ALTER TABLE isik_notifications_audience_device RENAME CONSTRAINT {device_unique} "
        "TO isik_notifications_audie_platform_token_37b4f8ea_uniq",
        f"DROP TRIGGER {guard} ON isik_subscription",
    )
    with connection.schema_editor() as editor:
        old_unique = editor._create_index_name("isik_notifications_audience_device", ["platform", "token"], "_uniq")
    execute(
        f"ALTER TABLE isik_notifications_audience_device RENAME CONSTRAINT "
        f"isik_notifications_audie_platform_token_37b4f8ea_uniq TO {old_unique}"
    )

    run("isik_notifications_audience")

    assert tables("isik_device", "isik_notifications_audience_device") == ["isik_device"]
    assert constraints(device_unique, old_unique) == [device_unique]
    assert guard in triggers("isik_subscription")


def test_a_name_that_is_neither_is_passed_over():
    execute("ALTER TABLE isik_idempotency_claim_with_body RENAME TO gone_claims")

    run("idempotency_with_body", RenamedTables({"IdempotencyClaimWithBody": "gone_claims"}, {"gone": "x"}))

    assert tables("isik_idempotency_claim_with_body") == ["isik_idempotency_claim_with_body"]


def test_a_swapped_model_has_no_table_of_isiks_to_rename(settings):
    execute("ALTER TABLE isik_device RENAME TO isik_notifications_audience_device")
    settings.ISIK_NOTIFICATIONS_DEVICE_MODEL = "testapp.Parcel"

    run("isik_notifications_audience")

    assert tables("isik_device", "isik_notifications_audience_device") == ["isik_notifications_audience_device"]
