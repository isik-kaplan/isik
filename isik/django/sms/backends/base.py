"""`BaseSMSBackend` - what every SMS sender is, and the one way a message is sent through one."""

from isik._internal.translation import gettext as _
from isik.django.sms import signals
from isik.django.sms.exceptions import (
    InvalidSender,
    SMSCancelSend,
    SMSError,
    SMSRecipientsRefused,
    SMSUnsupportedFeature,
)
from isik.django.sms.message import SendStatus, checked_number


# The options every sender takes.
COMMON_OPTIONS = ("fail_silently", "from_", "status_callback", "ignore_unsupported_features", "ignore_recipient_status")


class BaseSMSBackend:
    """
    An SMS sender. A subclass sends one message to one number - `send_one(message, recipient)`, answering a
    `RecipientStatus` - and says which of a message's optional fields it can send (`features`); this sends
    the rest of the way: `pre_send`, each number checked and sent to, `post_send`, and a message every number
    refused raised as `SMSRecipientsRefused`.

    Every sender takes these options, beside its own (`options`, which `isik.E024` holds `OPTIONS` to):

    - `fail_silently` - swallow an `SMSError` rather than raise it. A sender configured wrong still raises.
    - `from_` - the number, short code or sender id a message names none of is sent from.
    - `status_callback` - the URL delivery reports go to, for a message naming none.
    - `ignore_unsupported_features` - send a message asking what the sender can't do without it, rather than
      refuse it (`SMSUnsupportedFeature`).
    - `ignore_recipient_status` - don't raise `SMSRecipientsRefused` for a message every number refused.

    A sender is a context manager: `open()` on entering, `close()` on leaving, so one connection sends many
    messages. `send_messages()` opens and closes it itself when nothing has.
    """

    # The options a subclass takes, beside those every sender does.
    options = ()
    # The optional fields of a message this sender sends - see SMSMessage.features.
    features = frozenset()
    # Whether it sends nothing anywhere - so isik.W010 warns of it with DEBUG off.
    sends_nowhere = False
    # Whether it takes any option at all, using none it doesn't know - so isik.E024 holds OPTIONS to nothing.
    takes_any_option = False

    def __init__(
        self,
        *,
        alias="default",
        fail_silently=False,
        from_=None,
        status_callback=None,
        ignore_unsupported_features=False,
        ignore_recipient_status=False,
        **options,
    ):
        unknown = sorted(set(options) - set(self.options))
        if unknown:
            raise InvalidSender(
                _("%(backend)s doesn't take %(options)s.")
                % {"backend": type(self).__name__, "options": ", ".join(unknown)}
            )
        self.alias = alias
        self.fail_silently = fail_silently
        self.from_ = from_
        self.status_callback = status_callback
        self.ignore_unsupported_features = ignore_unsupported_features
        self.ignore_recipient_status = ignore_recipient_status

    @classmethod
    def check(cls, options):
        """What's wrong with `options` for this sender, as messages - `isik.E024` reports each."""
        return []

    def open(self):
        """Opens what sending reuses - answering whether it opened anything, for `send_messages()` to close."""
        return False

    def close(self):
        """Closes what `open()` opened."""

    def __enter__(self):
        try:
            self.open()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def send_messages(self, messages):
        """Sends each of `messages` - answering how many were sent, none of those `pre_send` cancelled."""
        if not messages:
            return 0
        opened = self.open()
        sent = 0
        try:
            for message in messages:
                try:
                    sent += self._send(message)
                except SMSError:
                    if not self.fail_silently:
                        raise
        finally:
            if opened:
                self.close()
        return sent

    def unsupported_feature(self, feature):
        """Refuses a message asking for `feature` - unless `ignore_unsupported_features` says to send it without."""
        if not self.ignore_unsupported_features:
            raise SMSUnsupportedFeature(
                _(
                    "%(backend)s can't send a message's %(feature)s - set ignore_unsupported_features to send it "
                    "without."
                )
                % {"backend": type(self).__name__, "feature": feature}
            )

    def check_features(self, message):
        """Refuses `message` for each optional field it sets that this sender can't send."""
        for feature in sorted(message.features - self.features):
            self.unsupported_feature(feature)

    def _send(self, message):
        try:
            signals.pre_send.send(sender=type(self), message=message, sender_alias=self.alias)
        except SMSCancelSend:
            return 0
        recipients = [checked_number(number) for number in message.to]
        self.check_features(message)
        status = SendStatus()
        for recipient in recipients:
            status.recipients[recipient] = self.send_one(message, recipient)
        message.send_status = status
        message.sent_using = self.alias
        signals.post_send.send(sender=type(self), message=message, status=status, sender_alias=self.alias)
        if status.refused and not self.ignore_recipient_status:
            raise SMSRecipientsRefused(
                _("Every number refused the message: %(numbers)s.")
                % {
                    "numbers": ", ".join(
                        _("%(number)s (%(status)s)") % {"number": number, "status": recipient.status.value}
                        for number, recipient in status.recipients.items()
                    )
                },
                status=status,
            )
        return 1

    def sender_of(self, message):
        """Who `message` is sent from - its own `from_`, else this sender's."""
        return message.from_ or self.from_

    def callback_of(self, message):
        """Where `message`'s delivery reports go - its own `status_callback`, else this sender's."""
        return message.status_callback or self.status_callback

    def send_one(self, message, recipient):
        """Sends `message` to `recipient` - answering its `RecipientStatus`."""
        raise NotImplementedError(_("A subclass of BaseSMSBackend sends one message to one number - send_one()."))
