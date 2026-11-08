"""
A sender through Twilio's Messages API - one HTTPS request a number, no SDK (`sms` extra):

    "default": {
        "BACKEND": "isik.django.sms.backends.twilio.SMSBackend",
        "OPTIONS": {
            "account_sid": os.environ["TWILIO_ACCOUNT_SID"],
            "auth_token": os.environ["TWILIO_AUTH_TOKEN"],
            "from_": "+14155550100",                 # or "messaging_service_sid": "MG..."
        },
    }

Options:

- `account_sid` - the account the messages are sent from.
- `auth_token`, or `api_key_sid` with `api_key_secret` - what the request is signed in with. The auth token is
  also what Twilio signs its webhooks with, so a project receiving them sets it either way.
- `from_` (every sender's) or `messaging_service_sid` - who it's sent from; a Messaging Service picks a number
  of its own, and is what scheduling (`send_at`) needs.
- `region` and `edge` - both or neither: Twilio's `api.<edge>.<region>.twilio.com`, for an account outside
  the US. Its credentials are that region's.
- `timeout` - seconds a request may take, 10 unless set.
- `retries` - how many more times a request that never reached Twilio is tried, 2 unless set. One that may
  have reached it is never tried again: Twilio takes no idempotency key, so it could be sent twice.
- `webhook_auth_tokens` - more tokens a webhook's signature may be made with, while one is rotated.
- `webhook_url` - the URL Twilio posts webhooks to, where the one a request arrives at isn't it (behind a
  proxy).
- `transport` - the httpx transport requests go through - `httpx.MockTransport` in a test.

A number Twilio refuses as no phone's is `Status.INVALID`; one that opted out, or a region the account can't
send to, `Status.REJECTED`. A rate-limited request raises `SMSRateLimited`, and every other refusal
`SMSAPIError` with Twilio's own code.
"""

from urllib.parse import urlencode, urlsplit, urlunsplit

from isik._internal import check_extra
from isik._internal.translation import gettext as _


check_extra("sms", "httpx")

import httpx  # noqa: E402 - after the extra is checked

from isik.django.sms.backends.base import BaseSMSBackend  # noqa: E402
from isik.django.sms.exceptions import SMSAPIError, SMSRateLimited  # noqa: E402
from isik.django.sms.message import RecipientStatus, Status  # noqa: E402


API_VERSION = "2010-04-01"
# Twilio's codes for a number it won't send to: not a phone's, not a mobile, unroutable - and one that opted out,
# or is in a region the account can't send to.
INVALID_CODES = {21211, 21614, 21612}
REJECTED_CODES = {21610, 21408}
RATE_LIMITED_CODE = 20429
STATUSES = {
    "accepted": Status.QUEUED,
    "queued": Status.QUEUED,
    "scheduled": Status.SCHEDULED,
    "sending": Status.SENT,
    "sent": Status.SENT,
    "delivered": Status.DELIVERED,
    "undelivered": Status.FAILED,
    "failed": Status.FAILED,
}


def with_metadata(url, metadata):
    """`url` with `metadata` added to its query - how it comes back with each delivery report."""
    if not metadata:
        return url
    parts = urlsplit(url)
    query = "&".join(part for part in (parts.query, urlencode(metadata)) if part)
    return urlunsplit(parts._replace(query=query))


class SMSBackend(BaseSMSBackend):
    """Twilio's Messages API - see the module's own docstring for its options."""

    options = (
        "account_sid",
        "auth_token",
        "api_key_sid",
        "api_key_secret",
        "messaging_service_sid",
        "region",
        "edge",
        "timeout",
        "retries",
        "webhook_auth_tokens",
        "webhook_url",
        "transport",
    )
    features = frozenset({"media_urls", "metadata", "send_at", "validity"})

    def __init__(
        self,
        *,
        account_sid,
        auth_token=None,
        api_key_sid=None,
        api_key_secret=None,
        messaging_service_sid=None,
        region=None,
        edge=None,
        timeout=10,
        retries=2,
        webhook_auth_tokens=(),
        webhook_url=None,
        transport=None,
        **options,
    ):
        super().__init__(**options)
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.api_key_sid = api_key_sid
        self.api_key_secret = api_key_secret
        self.messaging_service_sid = messaging_service_sid
        self.region = region
        self.edge = edge
        self.timeout = timeout
        self.retries = retries
        self.webhook_auth_tokens = tuple(webhook_auth_tokens)
        self.webhook_url = webhook_url
        self.transport = transport
        self.client = None

    @classmethod
    def check(cls, options):
        problems = []
        if not options.get("account_sid"):
            problems.append(_("twilio.SMSBackend sends from account_sid - set it."))
        if not options.get("auth_token") and not (options.get("api_key_sid") and options.get("api_key_secret")):
            problems.append(
                _("twilio.SMSBackend signs in with auth_token, or api_key_sid and api_key_secret - set them.")
            )
        if not options.get("from_") and not options.get("messaging_service_sid"):
            problems.append(_("twilio.SMSBackend sends from from_, or a messaging_service_sid - set one."))
        if bool(options.get("region")) != bool(options.get("edge")):
            problems.append(_("twilio.SMSBackend takes region and edge together - set both, or neither."))
        return problems

    @property
    def base_url(self):
        """Twilio's API, in the account's region where it names one."""
        if self.region:
            return f"https://api.{self.edge}.{self.region}.twilio.com"
        return "https://api.twilio.com"

    @property
    def messages_url(self):
        return f"{self.base_url}/{API_VERSION}/Accounts/{self.account_sid}/Messages.json"

    def webhook_tokens(self):
        """Every token a webhook's signature may be made with - the auth token, then the rotated ones."""
        return tuple(token for token in (self.auth_token, *self.webhook_auth_tokens) if token)

    def open(self):
        if self.client is not None:
            return False
        auth = (self.api_key_sid, self.api_key_secret) if self.api_key_sid else (self.account_sid, self.auth_token)
        self.client = httpx.Client(auth=auth, timeout=self.timeout, trust_env=False, transport=self.transport)
        return True

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def payload(self, message, recipient):
        """The form Twilio is posted for `message` to `recipient`."""
        data = {"To": recipient, "Body": message.body}
        sender = self.sender_of(message)
        if self.messaging_service_sid and not message.from_:
            data["MessagingServiceSid"] = self.messaging_service_sid
        else:
            data["From"] = sender
        callback = self.callback_of(message)
        if callback:
            data["StatusCallback"] = with_metadata(callback, message.metadata)
        elif message.metadata:
            self.unsupported_feature("metadata without a status_callback")
        if message.media_urls:
            data["MediaUrl"] = list(message.media_urls)
        if message.validity is not None:
            data["ValidityPeriod"] = message.validity_seconds()
        if message.send_at is not None:
            if "MessagingServiceSid" not in data:
                self.unsupported_feature("send_at without a messaging_service_sid")
            else:
                data["SendAt"] = message.send_at_aware().isoformat()
                data["ScheduleType"] = "fixed"
        return {**data, **message.provider_extra}

    def post(self, data):
        """Posts `data` - trying again, up to `retries` times, only a request that never reached Twilio."""
        attempt = 0
        while True:
            try:
                return self.client.post(self.messages_url, data=data)
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                if attempt == self.retries:
                    raise SMSAPIError(
                        _("Twilio couldn't be reached: %(error)s") % {"error": error}, retryable=True
                    ) from error
                attempt += 1
            except httpx.TransportError as error:
                raise SMSAPIError(
                    _("Twilio may or may not have the message - the request failed after it was sent: %(error)s")
                    % {"error": error}
                ) from error

    def send_one(self, message, recipient):
        data = self.payload(message, recipient)
        opened = self.open()
        try:
            response = self.post(data)
        finally:
            if opened:
                self.close()
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_success:
            return RecipientStatus(
                STATUSES.get(body.get("status"), Status.UNKNOWN),
                message_id=body.get("sid"),
                segments=int(body["num_segments"]) if body.get("num_segments") else None,
                price=body.get("price"),
                price_unit=body.get("price_unit"),
                error_code=str(body["error_code"]) if body.get("error_code") else None,
                error_message=body.get("error_message"),
                raw=body,
            )
        return self.refusal(response, body)

    def refusal(self, response, body):
        """What Twilio's refusal means - a number's status, or an error raised."""
        code = body.get("code")
        said = body.get("message") or response.reason_phrase
        if code in INVALID_CODES or code in REJECTED_CODES:
            return RecipientStatus(
                Status.INVALID if code in INVALID_CODES else Status.REJECTED,
                error_code=str(code),
                error_message=said,
                raw=body,
            )
        message = _("Twilio refused the message (%(status)s, code %(code)s): %(said)s") % {
            "status": response.status_code,
            "code": code,
            "said": said,
        }
        if response.status_code == 429 or code == RATE_LIMITED_CODE:
            raise SMSRateLimited(
                message,
                provider_code=code,
                status_code=response.status_code,
                response=body,
            )
        raise SMSAPIError(
            message,
            provider_code=code,
            status_code=response.status_code,
            retryable=response.status_code >= 500,
            response=body,
        )
