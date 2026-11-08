"""`SMSMessage`, and what sending one answers - `SendStatus`, a `RecipientStatus` per number."""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum

from django.utils import timezone

from isik._internal.translation import gettext as _
from isik.django.sms.exceptions import SMSInvalidNumber


E164 = re.compile(r"\+[1-9]\d{1,14}")


class Status(Enum):
    """Where a message to one number stands, in words every provider's answer is mapped to."""

    QUEUED = "queued"
    SCHEDULED = "scheduled"
    SENT = "sent"
    DELIVERED = "delivered"
    INVALID = "invalid"
    REJECTED = "rejected"
    FAILED = "failed"
    UNKNOWN = "unknown"


# A number the provider refused to send to at all - one not a phone's, or one that opted out.
REFUSED = {Status.INVALID, Status.REJECTED}


@dataclass
class RecipientStatus:
    """What the provider answered for one number: its id for the message, and how it stands."""

    status: Status
    message_id: str | None = None
    segments: int | None = None
    price: str | None = None
    price_unit: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw: object = None


@dataclass
class SendStatus:
    """What sending a message answered - `{number: RecipientStatus}` for each number it went to."""

    recipients: dict = field(default_factory=dict)

    @property
    def message_ids(self):
        """Every id the provider gave the message, one per number it accepted."""
        return {status.message_id for status in self.recipients.values() if status.message_id is not None}

    @property
    def statuses(self):
        """Each `Status` a number's message has."""
        return {status.status for status in self.recipients.values()}

    @property
    def refused(self):
        """Whether every number was refused - invalid, or opted out."""
        return bool(self.recipients) and self.statuses <= REFUSED


def checked_number(number):
    """`number`, an E.164 phone number - `+` and up to 15 digits. isik checks the form, not the number."""
    if not isinstance(number, str) or not E164.fullmatch(number):
        raise SMSInvalidNumber(
            _("%(number)r isn't an E.164 phone number - a + and up to 15 digits, such as +14155550123.")
            % {"number": number}
        )
    return number


def aware(moment):
    """`moment`, a datetime, in the current time zone when it names none."""
    if moment is None or timezone.is_aware(moment):
        return moment
    return timezone.make_aware(moment)


class SMSMessage:
    """
    A text message, sent to each number in `to` - one message a number, each with its own status:

        SMSMessage("+14155550123", "Your code is 123456").send()
        SMSMessage(["+14155550123", "+447700900123"], "Doors open at 7.", using="marketing").send()

    `from_` is a number, a short code or an alphanumeric sender id - the sender's own `from_` unless given.
    `media_urls` makes it an MMS where the provider sends one. `metadata` comes back with the provider's
    delivery reports. `send_at` schedules it, `validity` is how long the provider keeps trying,
    `status_callback` is the URL delivery reports go to over the sender's own, and `message_type`
    (`"transactional"` or `"promotional"`) tells a provider routing by it. `provider_extra` is merged into
    the provider's own request last, for what only it has.

    Once sent, `send_status` is what the provider answered and `sent_using` the sender it went through.
    """

    def __init__(
        self,
        to,
        body,
        from_=None,
        *,
        media_urls=(),
        metadata=None,
        send_at=None,
        validity=None,
        status_callback=None,
        message_type=None,
        provider_extra=None,
        using="default",
    ):
        self.to = [to] if isinstance(to, str) else list(to)
        self.body = body
        self.from_ = from_
        self.media_urls = list(media_urls)
        self.metadata = dict(metadata or {})
        self.send_at = send_at
        self.validity = validity
        self.status_callback = status_callback
        self.message_type = message_type
        self.provider_extra = dict(provider_extra or {})
        self.using = using
        self.send_status = None
        self.sent_using = None

    def __repr__(self):
        return f"<SMSMessage to={self.to!r} body={self.body!r}>"

    @property
    def features(self):
        """The optional fields this message sets - each a feature a sender has or doesn't."""
        return {
            name
            for name, value in (
                ("media_urls", self.media_urls),
                ("metadata", self.metadata),
                ("send_at", self.send_at),
                ("validity", self.validity),
                ("message_type", self.message_type),
            )
            if value
        }

    def validity_seconds(self):
        """`validity` in whole seconds - None when unset."""
        if self.validity is None:
            return None
        return int(self.validity.total_seconds() if isinstance(self.validity, timedelta) else self.validity)

    def send_at_aware(self):
        """`send_at`, aware - see `aware()`."""
        return aware(self.send_at)

    def send(self):
        """Sends it through its sender, `using` - 1 once it's sent, 0 when a `pre_send` receiver cancelled it."""
        from isik.django.sms.senders import senders

        with senders[self.using] as sender:
            return sender.send_messages([self])
