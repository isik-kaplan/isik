"""
What sending an SMS raises. `SMSError` is everything a sender's `fail_silently` swallows; a sender that's
configured wrong is `ImproperlyConfigured` instead, which nothing swallows.
"""

from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation


class SMSError(Exception):
    """A message that couldn't be sent - what `fail_silently` swallows."""


class SMSAPIError(SMSError):
    """
    The provider refused the request, or couldn't be reached. `provider_code` is its own code for why,
    `status_code` the HTTP status, and `retryable` whether trying again - or another provider - can work:
    the request never reached it, it failed on its side, or it was rate-limited.
    """

    def __init__(self, message, *, provider_code=None, status_code=None, retryable=False, response=None):
        super().__init__(message)
        self.provider_code = provider_code
        self.status_code = status_code
        self.retryable = retryable
        self.response = response


class SMSRateLimited(SMSAPIError):
    """The provider is refusing requests for now - `retry_after` seconds, where it said."""

    def __init__(self, message, *, retry_after=None, **kwargs):
        super().__init__(message, retryable=True, **kwargs)
        self.retry_after = retry_after


class SMSRecipientsRefused(SMSError):
    """Every number was refused - invalid, or opted out. `status` says why, number by number."""

    def __init__(self, message, *, status):
        super().__init__(message)
        self.status = status


class SMSInvalidNumber(SMSError, ValueError):
    """A number that isn't in E.164 form."""


class SMSUnsupportedFeature(SMSError, ValueError):
    """A message asking what its sender can't do - `ignore_unsupported_features` sends it without."""


class SMSCancelSend(Exception):  # noqa: N818 - a signal to stop, not an error
    """Raised by a `pre_send` receiver to send the message nowhere."""


class SMSWebhookValidationFailure(SuspiciousOperation):
    """A webhook request whose signature isn't the provider's - Django answers it with a 400."""


class SenderDoesNotExist(ImproperlyConfigured):
    """`senders[alias]` for an alias `ISIK['DJANGO']['SMS']` doesn't configure."""


class InvalidSender(ImproperlyConfigured):
    """A sender `ISIK['DJANGO']['SMS']` configures wrong."""
