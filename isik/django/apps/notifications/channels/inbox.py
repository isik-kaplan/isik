from isik.django.apps.notifications.audience.swapping import get_inbox_item_model
from isik.django.apps.notifications.channels import Channel, Outcome, Target
from isik.django.apps.notifications.partitions import in_partition
from isik.django.apps.notifications.swapping import get_inbox_delivery_model


class InboxChannel(Channel):
    """
    A user's in-app inbox. Its item is keyed by the delivery, so a delivery tried twice lands once - and,
    the inbox and the ledger sharing a database, landing is all there is to sending.
    """

    name = "inbox"

    def targets(self, notice, kind, users):
        return [Target(f"user:{user.pk}", get_inbox_delivery_model(), {"user_id": str(user.pk)}) for user in users]

    def send(self, delivery):
        notice = delivery.notice
        with in_partition(delivery.partition):
            get_inbox_item_model()._default_manager.get_or_create(
                delivery_id=delivery.id,
                defaults={
                    "user_id": delivery.inbox.user_id,
                    "kind_id": notice.kind_id,
                    "variant": notice.variant,
                    "subject_label": notice.subject_label,
                    "subject_id": notice.subject_id,
                    "actor_id": notice.actor_id,
                    "context": notice.context,
                    "payload": {"data": notice.snapshot, "previous": notice.previous},
                },
            )
        return Outcome(succeeded=True)
