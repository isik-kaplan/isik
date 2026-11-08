"""A sender writing each message as one line - to stdout, unless given a stream."""

import sys
import threading

from isik.django.sms.backends.base import BaseSMSBackend
from isik.django.sms.message import RecipientStatus, Status


def one_line(message, recipient, sender):
    """`message` to `recipient` as one line - its body's own line breaks written as `\\n`."""
    body = message.body.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
    return f"SMS from {sender or '-'} to {recipient}: {body}"


class SMSBackend(BaseSMSBackend):
    """Each message written as a line - where development shows what it would have sent."""

    options = ("stream",)
    features = frozenset({"media_urls", "metadata", "send_at", "validity", "message_type"})
    sends_nowhere = True

    def __init__(self, *, stream=None, **options):
        super().__init__(**options)
        self.stream = stream or sys.stdout
        self._lock = threading.RLock()

    def send_one(self, message, recipient):
        with self._lock:
            self.stream.write(one_line(message, recipient, self.sender_of(message)) + "\n")
            self.stream.flush()
        return RecipientStatus(Status.SENT)
