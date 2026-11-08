"""
Twilio's webhooks: a message's delivery reports, and messages sent to the project's number. Each view is
given the sender whose auth token checks the request's signature:

    path("sms/twilio/status/", TwilioStatusView.as_view(using="default")),
    path("sms/twilio/inbound/", TwilioInboundView.as_view(using="default")),

A request whose `X-Twilio-Signature` isn't made with the sender's auth token, or one of its
`webhook_auth_tokens`, is refused with a 400. Behind a proxy, the URL Twilio signed isn't the one the request
arrives at - set the sender's `webhook_url` to it.
"""

import base64
import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit, urlunsplit

from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from isik._internal.translation import gettext as _
from isik.django.sms import signals
from isik.django.sms.exceptions import InvalidSender, SMSWebhookValidationFailure
from isik.django.sms.message import Status
from isik.django.sms.webhooks import DeliveryEvent, InboundEvent


DELIVERY_STATUSES = {
    "accepted": Status.QUEUED,
    "queued": Status.QUEUED,
    "scheduled": Status.SCHEDULED,
    "canceled": Status.FAILED,
    "sending": Status.SENT,
    "sent": Status.SENT,
    "delivered": Status.DELIVERED,
    "read": Status.DELIVERED,
    "undelivered": Status.FAILED,
    "failed": Status.FAILED,
}
# What a message asks to opt out with, where Twilio's own OptOutType doesn't say.
OPT_OUT_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"}
# The parameters isik's own requests carry, rather than a project's metadata.
TWILIO_QUERY = {"bodySHA256"}


def signature(token, url, params):
    """Twilio's signature of a request: HMAC-SHA1 of `url` and each parameter's name and values, sorted."""
    payload = url + "".join(name + value for name in sorted(params) for value in sorted(set(params[name])))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def _port_variants(url):
    # Twilio signs the URL with or without its default port, depending on where the signature was made.
    parts = urlsplit(url)
    default = {"https": 443, "http": 80}.get(parts.scheme)
    if parts.port is None and default:
        return [url, urlunsplit(parts._replace(netloc=f"{parts.hostname}:{default}"))]
    if parts.port == default:
        return [url, urlunsplit(parts._replace(netloc=parts.hostname))]
    return [url]


def signed_by(tokens, url, params, given, body=b""):
    """
    Whether `given` is Twilio's signature of the request with one of `tokens`. A JSON body is signed by its hash,
    in the URL's `bodySHA256` - and with no parameters, which the caller passes as `{}`.
    """
    if not given:
        return False
    query = parse_qs(urlsplit(url).query)
    if "bodySHA256" in query and not hmac.compare_digest(hashlib.sha256(body).hexdigest(), query["bodySHA256"][0]):
        return False
    return any(
        hmac.compare_digest(signature(token, variant, params), given)
        for token in tokens
        for variant in _port_variants(url)
    )


@method_decorator(csrf_exempt, name="dispatch")
class TwilioWebhookView(View):
    """A Twilio webhook - its signature checked with the sender `using`'s tokens before `handle()` sees it."""

    http_method_names = ["post"]
    using = "default"

    def sender(self):
        from isik.django.sms.backends.twilio import SMSBackend
        from isik.django.sms.senders import senders

        sender = senders[self.using]
        if not isinstance(sender, SMSBackend):
            raise InvalidSender(_("%(alias)r isn't a Twilio sender.") % {"alias": self.using})
        if not sender.webhook_tokens():
            raise InvalidSender(
                _("%(alias)r has no auth_token to check Twilio's signatures with - set it.") % {"alias": self.using}
            )
        return sender

    def url(self, request, sender):
        """The URL Twilio signed - the sender's `webhook_url`, with the request's own query, else the request's."""
        if not sender.webhook_url:
            return request.build_absolute_uri()
        query = request.META.get("QUERY_STRING")
        return f"{sender.webhook_url}?{query}" if query else sender.webhook_url

    def post(self, request, *args, **kwargs):
        sender = self.sender()
        tokens, url, given = (
            sender.webhook_tokens(),
            self.url(request, sender),
            request.META.get("HTTP_X_TWILIO_SIGNATURE"),
        )
        if "bodySHA256" in request.GET:
            # A JSON body is signed by its hash, with no parameters - read before anything parses it.
            signed = signed_by(tokens, url, {}, given, request.body)
        else:
            signed = signed_by(tokens, url, {name: request.POST.getlist(name) for name in request.POST}, given)
        if not signed:
            raise SMSWebhookValidationFailure(_("The request's X-Twilio-Signature isn't Twilio's."))
        return self.handle(request, sender)

    def handle(self, request, sender):
        raise NotImplementedError(_("A Twilio webhook view handles a request it checked - handle()."))


class TwilioStatusView(TwilioWebhookView):
    """A message's delivery report, sent as `delivery` - the metadata it was sent with included."""

    def handle(self, request, sender):
        posted = request.POST
        event = DeliveryEvent(
            provider="twilio",
            message_id=posted.get("MessageSid") or posted.get("SmsSid", ""),
            status=DELIVERY_STATUSES.get(posted.get("MessageStatus") or posted.get("SmsStatus"), Status.UNKNOWN),
            recipient=posted.get("To"),
            error_code=posted.get("ErrorCode") or None,
            metadata={name: value for name, value in request.GET.items() if name not in TWILIO_QUERY},
            raw=posted.dict(),
        )
        signals.delivery.send(sender=type(sender), event=event, sender_alias=sender.alias)
        return HttpResponse(status=204)


class TwilioInboundView(TwilioWebhookView):
    """A message sent to the project's number, sent as `inbound` - answered with nothing sent back."""

    def handle(self, request, sender):
        posted = request.POST
        body = posted.get("Body", "")
        opt_out = posted.get("OptOutType")
        event = InboundEvent(
            provider="twilio",
            message_id=posted.get("MessageSid") or None,
            from_=posted.get("From", ""),
            to=posted.get("To", ""),
            body=body,
            media_urls=[posted[f"MediaUrl{index}"] for index in range(int(posted.get("NumMedia") or 0))],
            is_opt_out=opt_out == "STOP" if opt_out else body.strip().upper() in OPT_OUT_KEYWORDS,
            raw=posted.dict(),
        )
        signals.inbound.send(sender=type(sender), event=event, sender_alias=sender.alias)
        return HttpResponse("<Response/>", content_type="text/xml")
