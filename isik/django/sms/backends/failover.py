"""A sender trying several, in order - the next one only when the one before couldn't be reached."""

from isik._internal.translation import gettext as _
from isik.django.sms.backends.base import BaseSMSBackend
from isik.django.sms.exceptions import SMSAPIError


class SMSBackend(BaseSMSBackend):
    """
    Each number sent through the first of `senders` - aliases of other configured senders - that takes it:

        "default": {
            "BACKEND": "isik.django.sms.backends.failover.SMSBackend",
            "OPTIONS": {"senders": ["twilio", "backup"]},
        }

    The next sender is tried only when one raises a retryable `SMSAPIError` - it couldn't be reached, failed on
    its side, or rate-limited the request. A number one refused, or a message it can't send, is that sender's
    answer, and no other is asked. Each sender's own options - `from_` among them - are its own.
    """

    options = ("senders",)
    features = frozenset({"media_urls", "metadata", "send_at", "validity", "message_type"})

    def __init__(self, *, senders, **options):
        from isik.django.sms.senders import senders as configured

        super().__init__(**options)
        self.senders = [configured[alias] for alias in senders]

    @classmethod
    def check(cls, options):
        names = options.get("senders")
        if not names or isinstance(names, str):
            return [_("failover.SMSBackend tries senders, a list of the aliases of other senders - set it.")]
        return []

    def open(self):
        self._opened = [sender for sender in self.senders if sender.open()]
        return bool(self._opened)

    def close(self):
        for sender in getattr(self, "_opened", ()):
            sender.close()
        self._opened = []

    def check_features(self, message):
        """Nothing here - each sender refuses what it can't send itself."""

    def send_one(self, message, recipient):
        *earlier, last = self.senders
        for sender in earlier:
            try:
                sender.check_features(message)
                return sender.send_one(message, recipient)
            except SMSAPIError as error:
                if not error.retryable:
                    raise
        last.check_features(message)
        return last.send_one(message, recipient)
