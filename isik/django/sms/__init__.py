"""
`isik.django.sms` - text messages sent through a configured sender, the way Django sends email through its
mailers: each sender an alias in `ISIK['DJANGO']['SMS']`, with its `BACKEND` and the `OPTIONS` it's built with.

    ISIK = {"DJANGO": {"SMS": {
        "default": {"BACKEND": "isik.django.sms.backends.twilio.SMSBackend", "OPTIONS": {...}},
        "marketing": {...},
    }}}

    send_sms("+14155550123", "Your code is 123456")
    send_sms(numbers, "Doors open at 7.", using="marketing")

`outbox` is where the locmem sender keeps each message it sent - every configured sender's, during a test run.
See `message`, `senders`, `backends`, `signals`, `webhooks` and `segments`.
"""

from isik.django.sms.exceptions import (
    InvalidSender,
    SenderDoesNotExist,
    SMSAPIError,
    SMSCancelSend,
    SMSError,
    SMSInvalidNumber,
    SMSRateLimited,
    SMSRecipientsRefused,
    SMSUnsupportedFeature,
    SMSWebhookValidationFailure,
)
from isik.django.sms.message import RecipientStatus, SendStatus, SMSMessage, Status
from isik.django.sms.segments import Segments, segments
from isik.django.sms.senders import send_sms, senders


__all__ = [
    "InvalidSender",
    "RecipientStatus",
    "SMSAPIError",
    "SMSCancelSend",
    "SMSError",
    "SMSInvalidNumber",
    "SMSMessage",
    "SMSRateLimited",
    "SMSRecipientsRefused",
    "SMSUnsupportedFeature",
    "SMSWebhookValidationFailure",
    "Segments",
    "SendStatus",
    "SenderDoesNotExist",
    "Status",
    "segments",
    "send_sms",
    "senders",
]
