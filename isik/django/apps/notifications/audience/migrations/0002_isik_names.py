# A database migrated before isik 0.43.0 holds this app's tables under their old names, and took the new
# 0001_initial - named as the old one was - as applied: this brings it onto 0.43.0's. See RenamedTables.
from django.db import migrations

from isik.django.apps.common.db.renaming import RenamedTables


class Migration(migrations.Migration):
    dependencies = [("isik_notifications_audience", "0001_initial")]

    operations = [
        RenamedTables(
            {
                "Subscription": "isik_notifications_audience_subscription",
                "InboxItem": "isik_notifications_audience_inboxitem",
                "Endpoint": "isik_notifications_audience_endpoint",
                "EndpointKind": "isik_notifications_audience_endpointkind",
                "Device": "isik_notifications_audience_device",
            },
            {
                "isik_notifications_audience_subscription_once": "isik_subscription_once",
                "isik_notifications_audience_endpointkind_once": "isik_endpoint_kind_once",
            },
        ),
    ]
