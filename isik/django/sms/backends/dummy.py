"""A sender sending each message nowhere."""

from isik.django.sms.backends.base import BaseSMSBackend
from isik.django.sms.message import RecipientStatus, Status


class SMSBackend(BaseSMSBackend):
    """Each message sent nowhere, and answered as sent."""

    features = frozenset({"media_urls", "metadata", "send_at", "validity", "message_type"})
    sends_nowhere = True

    def send_one(self, message, recipient):
        return RecipientStatus(Status.SENT)
