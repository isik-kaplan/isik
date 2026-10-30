# A database migrated before isik 0.43.0 holds this app's tables under their old names, and took the new
# 0001_initial - named as the old one was - as applied: this brings it onto 0.43.0's. See RenamedTables.
from django.db import migrations

from isik.django.apps.common.db.renaming import RenamedTables


class Migration(migrations.Migration):
    dependencies = [("isik_features", "0001_initial")]

    operations = [
        RenamedTables(
            {
                "Feature": "isik_features_feature",
                "FeatureGrant": "isik_features_featuregrant",
                "SweepCursor": "isik_features_sweepcursor",
            },
            {},
        ),
    ]
