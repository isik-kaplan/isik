"""A sender appending each message to a file as a JSON line - for an end-to-end run to read back."""

import json
import threading
from pathlib import Path

from isik._internal.translation import gettext as _
from isik.django.sms.backends.base import BaseSMSBackend
from isik.django.sms.message import RecipientStatus, Status


class SMSBackend(BaseSMSBackend):
    """Each message to each number a JSON line appended to `file_path`, the directory created where it's missing."""

    options = ("file_path",)
    features = frozenset({"media_urls", "metadata", "send_at", "validity", "message_type"})
    sends_nowhere = True

    def __init__(self, *, file_path, **options):
        super().__init__(**options)
        self.file_path = Path(file_path)
        self._lock = threading.Lock()

    @classmethod
    def check(cls, options):
        return [] if options.get("file_path") else [_("filebased.SMSBackend writes to file_path - set it.")]

    def send_one(self, message, recipient):
        send_at = message.send_at_aware()
        line = {
            "to": recipient,
            "from": self.sender_of(message),
            "body": message.body,
            "media_urls": message.media_urls,
            "metadata": message.metadata,
            "send_at": send_at.isoformat() if send_at else None,
            "validity": message.validity_seconds(),
            "message_type": message.message_type,
            "sent_using": self.alias,
        }
        with self._lock:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("ab") as file:
                file.write((json.dumps(line) + "\n").encode())
        return RecipientStatus(Status.SENT)
