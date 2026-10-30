# A database migrated before isik 0.43.0 holds this app's tables under their old names, and took the new
# 0001_initial - named as the old one was - as applied: this brings it onto 0.43.0's. See RenamedTables.
from django.db import migrations

from isik.django.apps.common.db.renaming import RenamedTables


class Migration(migrations.Migration):
    dependencies = [("idempotency_with_body", "0001_initial")]

    operations = [
        RenamedTables(
            {
                "IdempotencyClaimWithBody": "idempotency_with_body_idempotencyclaimwithbody",
            },
            {
                "idempotency_with_body_idempotencyclaimwithbody_key": "isik_idempotency_claim_with_body_key",
            },
        ),
    ]
