# A database migrated before isik 0.43.0 holds this app's tables under their old names, and took the new
# 0001_initial - named as the old one was - as applied: this brings it onto 0.43.0's. See RenamedTables.
from django.db import migrations

from isik.django.apps.common.db.renaming import RenamedTables


class Migration(migrations.Migration):
    dependencies = [("idempotency_by_reference", "0001_initial")]

    operations = [
        RenamedTables(
            {
                "IdempotencyClaim": "idempotency_by_reference_idempotencyclaim",
            },
            {
                "idempotency_by_reference_idempotencyclaim_key": "isik_idempotency_claim_key",
            },
        ),
    ]
