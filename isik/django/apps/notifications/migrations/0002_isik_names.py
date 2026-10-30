# A database migrated before isik 0.43.0 holds this app's tables under their old names, and took the new
# 0001_initial - named as the old one was - as applied: this brings it onto 0.43.0's. See RenamedTables.
from django.db import migrations

from isik.django.apps.common.db.renaming import RenamedTables


class Migration(migrations.Migration):
    dependencies = [("isik_notifications", "0001_initial")]

    operations = [
        RenamedTables(
            {
                "NoticeKindPolicy": "isik_notifications_noticekindpolicy",
                "Notice": "isik_notifications_notice",
                "FanOutShard": "isik_notifications_fanoutshard",
                "Delivery": "isik_notifications_delivery",
                "WebhookDelivery": "isik_notifications_webhookdelivery",
                "EmailDelivery": "isik_notifications_emaildelivery",
                "InboxDelivery": "isik_notifications_inboxdelivery",
                "PushDelivery": "isik_notifications_pushdelivery",
                "DeliveryAttempt": "isik_notifications_deliveryattempt",
            },
            {
                "isik_notifications_delivery_once": "isik_delivery_once",
                "isik_notifications_deliveryattempt_once": "isik_delivery_attempt_once",
                "isik_notifications_fanoutshard_once": "isik_fan_out_shard_once",
                "delivery_due_c7f4e1c9": "isik_delivery_due",
                "notice_unfanned_84e28463": "isik_notice_unfanned",
                "shard_unfinished_be4d0b9a": "isik_fan_out_shard_unfinished",
            },
        ),
    ]
