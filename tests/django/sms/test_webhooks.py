"""Twilio's webhooks - signatures, delivery reports and incoming messages - and supersedes()."""

import base64
import hashlib
import hmac

import pytest
from django.core.exceptions import SuspiciousOperation
from django.test import RequestFactory
from django.test.client import encode_multipart

from isik.django.sms import InvalidSender, SMSWebhookValidationFailure, Status, signals
from isik.django.sms.webhooks import RANKS, DeliveryEvent, InboundEvent, supersedes
from isik.django.sms.webhooks.twilio import (
    TwilioInboundView,
    TwilioStatusView,
    TwilioWebhookView,
    signature,
    signed_by,
)


TWILIO = "isik.django.sms.backends.twilio.SMSBackend"
URL = "http://testserver/sms/status/"


def expected(token, url, params):
    """Twilio's documented algorithm, written out apart from isik's."""
    data = url
    for name in sorted(params):
        for value in sorted(set(params[name])):
            data += name + value
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


@pytest.fixture
def twilio(senders_configured):
    def configure(**options):
        senders_configured(
            {
                "default": {
                    "BACKEND": TWILIO,
                    "OPTIONS": {"account_sid": "AC", "auth_token": "token", "from_": "+1", **options},
                }
            }
        )

    configure()
    return configure


@pytest.fixture
def heard():
    events = []

    def listen(sender, event, sender_alias, **kwargs):
        events.append((sender.__name__, event, sender_alias))

    signals.delivery.connect(listen)
    signals.inbound.connect(listen)
    yield events
    signals.delivery.disconnect(listen)
    signals.inbound.disconnect(listen)


def post(view, data, *, token="token", url=URL, path="/sms/status/", query="", sign=None, **view_options):
    """Posts `data` to `view`, signed as Twilio would sign it unless `sign` says otherwise."""
    request = RequestFactory().post(path + (f"?{query}" if query else ""), data)
    params = {name: request.POST.getlist(name) for name in request.POST}
    request.META["HTTP_X_TWILIO_SIGNATURE"] = (
        sign if sign is not None else expected(token, url + (f"?{query}" if query else ""), params)
    )
    return view.as_view(**view_options)(request)


class TestSignature:
    def test_is_twilios_algorithm(self):
        params = {"To": ["+1"], "Body": ["Hi"], "MediaUrl0": ["b", "a", "a"]}

        assert signature("token", URL, params) == expected("token", URL, params)

    def test_known_answer(self):
        # Twilio's own documented example: https://www.twilio.com/docs/usage/security#validating-requests
        params = {
            "CallSid": ["CA1234567890ABCDE"],
            "Caller": ["+12349013030"],
            "Digits": ["1234"],
            "From": ["+12349013030"],
            "To": ["+18005551212"],
        }

        assert (
            signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", params) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
        )

    def test_signed_with_any_token(self):
        params = {"Body": ["Hi"]}

        assert signed_by(("old", "new"), URL, params, expected("new", URL, params))
        assert not signed_by(("old",), URL, params, expected("new", URL, params))

    @pytest.mark.parametrize("given", [None, ""])
    def test_no_signature(self, given):
        assert not signed_by(("token",), URL, {}, given)

    @pytest.mark.parametrize(
        ("signed", "arrived"),
        [
            ("https://acme.com:443/cb", "https://acme.com/cb"),
            ("https://acme.com/cb", "https://acme.com:443/cb"),
            ("http://acme.com:80/cb", "http://acme.com/cb"),
            ("http://acme.com/cb", "http://acme.com:80/cb"),
        ],
    )
    def test_with_or_without_the_default_port(self, signed, arrived):
        assert signed_by(("token",), arrived, {}, expected("token", signed, {}))

    def test_another_port_is_another_url(self):
        assert not signed_by(("token",), "https://acme.com:8443/cb", {}, expected("token", "https://acme.com/cb", {}))
        assert signed_by(("token",), "https://acme.com:8443/cb", {}, expected("token", "https://acme.com:8443/cb", {}))

    def test_a_scheme_without_a_default_port(self):
        assert signed_by(("token",), "ftp://acme.com/cb", {}, expected("token", "ftp://acme.com/cb", {}))

    def test_a_json_body_by_its_hash(self):
        body = b'{"MessageSid": "SM1"}'
        url = f"{URL}?bodySHA256={hashlib.sha256(body).hexdigest()}"

        assert signed_by(("token",), url, {}, expected("token", url, {}), body)
        assert not signed_by(("token",), url, {}, expected("token", url, {}), b"{}")

    def test_an_empty_body_by_its_hash(self):
        url = f"{URL}?bodySHA256={hashlib.sha256(b'').hexdigest()}"

        assert signed_by(("token",), url, {}, expected("token", url, {}))


class TestTheStatusView:
    def test_a_delivery_report(self, twilio, heard):
        data = {"MessageSid": "SM1", "MessageStatus": "delivered", "To": "+14155550123", "From": "+1"}

        response = post(TwilioStatusView, data, query="invite=7&kind=a")

        assert response.status_code == 204
        ((sender, event, alias),) = heard
        assert (sender, alias) == ("SMSBackend", "default")
        assert event == DeliveryEvent(
            provider="twilio",
            message_id="SM1",
            status=Status.DELIVERED,
            recipient="+14155550123",
            error_code=None,
            metadata={"invite": "7", "kind": "a"},
            raw=data,
        )

    def test_a_failure(self, twilio, heard):
        post(TwilioStatusView, {"SmsSid": "SM1", "SmsStatus": "undelivered", "ErrorCode": "30003"})

        event = heard[0][1]
        assert (event.message_id, event.status, event.error_code, event.recipient) == (
            "SM1",
            Status.FAILED,
            "30003",
            None,
        )

    @pytest.mark.parametrize(
        ("said", "status"),
        [
            ("accepted", Status.QUEUED),
            ("queued", Status.QUEUED),
            ("scheduled", Status.SCHEDULED),
            ("canceled", Status.FAILED),
            ("sending", Status.SENT),
            ("sent", Status.SENT),
            ("delivered", Status.DELIVERED),
            ("read", Status.DELIVERED),
            ("undelivered", Status.FAILED),
            ("failed", Status.FAILED),
            ("partially_delivered", Status.UNKNOWN),
        ],
    )
    def test_its_status(self, twilio, heard, said, status):
        post(TwilioStatusView, {"MessageSid": "SM1", "MessageStatus": said})

        assert heard[0][1].status is status

    def test_no_sid(self, twilio, heard):
        post(TwilioStatusView, {"MessageStatus": "sent"})

        assert heard[0][1].message_id == ""

    def test_the_body_hash_isnt_metadata(self, twilio, heard):
        body = encode_multipart("BoUnDaRy", {})
        digest = hashlib.sha256(body.encode() if isinstance(body, str) else body).hexdigest()
        request = RequestFactory().post(
            f"/sms/status/?bodySHA256={digest}&invite=7", body, content_type="multipart/form-data; boundary=BoUnDaRy"
        )
        request.META["HTTP_X_TWILIO_SIGNATURE"] = expected("token", f"{URL}?bodySHA256={digest}&invite=7", {})

        assert TwilioStatusView.as_view()(request).status_code == 204
        assert heard[0][1].metadata == {"invite": "7"}

    def test_a_signature_that_isnt_twilios(self, twilio, heard):
        with pytest.raises(SMSWebhookValidationFailure) as refused:
            post(TwilioStatusView, {"MessageSid": "SM1"}, sign="forged")

        assert str(refused.value) == "The request's X-Twilio-Signature isn't Twilio's."
        assert isinstance(refused.value, SuspiciousOperation)  # which Django answers with a 400
        assert heard == []

    def test_a_rotated_token(self, twilio, heard):
        twilio(webhook_auth_tokens=["new"])

        assert post(TwilioStatusView, {"MessageSid": "SM1"}, token="new").status_code == 204

    def test_behind_a_proxy(self, twilio, heard):
        twilio(webhook_url="https://acme.com/sms/status/")

        assert post(TwilioStatusView, {"MessageSid": "SM1"}, url="https://acme.com/sms/status/").status_code == 204
        with pytest.raises(SMSWebhookValidationFailure):
            post(TwilioStatusView, {"MessageSid": "SM1"})

    def test_behind_a_proxy_with_the_metadata(self, twilio, heard):
        twilio(webhook_url="https://acme.com/sms/status/")

        response = post(TwilioStatusView, {"MessageSid": "SM1"}, url="https://acme.com/sms/status/", query="a=1")

        assert response.status_code == 204
        assert heard[0][1].metadata == {"a": "1"}

    def test_another_sender(self, senders_configured, heard):
        senders_configured(
            {
                "default": {"BACKEND": "isik.django.sms.backends.dummy.SMSBackend"},
                "us": {"BACKEND": TWILIO, "OPTIONS": {"account_sid": "AC", "auth_token": "us-token", "from_": "+1"}},
            }
        )

        assert post(TwilioStatusView, {"MessageSid": "SM1"}, token="us-token", using="us").status_code == 204
        assert heard[0][2] == "us"

    def test_only_a_post(self, twilio):
        assert TwilioStatusView.as_view()(RequestFactory().get("/sms/status/")).status_code == 405

    def test_a_sender_that_isnt_twilios(self, senders_configured):
        senders_configured({"default": {"BACKEND": "isik.django.sms.backends.dummy.SMSBackend"}})

        with pytest.raises(InvalidSender) as refused:
            post(TwilioStatusView, {})

        assert str(refused.value) == "'default' isn't a Twilio sender."

    def test_a_sender_with_no_auth_token(self, twilio):
        twilio(auth_token=None, api_key_sid="SK", api_key_secret="s")

        with pytest.raises(InvalidSender) as refused:
            post(TwilioStatusView, {})

        assert str(refused.value) == "'default' has no auth_token to check Twilio's signatures with - set it."

    def test_csrf_exempt(self, twilio, heard):
        request = RequestFactory().post("/sms/status/", {"MessageSid": "SM1"})
        request.META["HTTP_X_TWILIO_SIGNATURE"] = expected("token", URL, {"MessageSid": ["SM1"]})
        request._dont_enforce_csrf_checks = False

        assert TwilioStatusView.as_view().csrf_exempt is True
        assert TwilioStatusView.as_view()(request).status_code == 204

    def test_handle_is_a_subclasss(self, twilio):
        with pytest.raises(NotImplementedError) as refused:
            post(TwilioWebhookView, {})

        assert str(refused.value) == "A Twilio webhook view handles a request it checked - handle()."


class TestTheInboundView:
    def test_a_message(self, twilio, heard):
        data = {
            "MessageSid": "SM1",
            "From": "+14155550123",
            "To": "+14155550100",
            "Body": "Hello",
            "NumMedia": "2",
            "MediaUrl0": "https://x/0",
            "MediaUrl1": "https://x/1",
        }

        response = post(TwilioInboundView, data)

        assert (response.status_code, response["Content-Type"], response.content) == (200, "text/xml", b"<Response/>")
        ((sender, event, alias),) = heard
        assert (sender, alias) == ("SMSBackend", "default")
        assert event == InboundEvent(
            provider="twilio",
            message_id="SM1",
            from_="+14155550123",
            to="+14155550100",
            body="Hello",
            media_urls=["https://x/0", "https://x/1"],
            is_opt_out=False,
            raw=data,
        )

    def test_next_to_nothing(self, twilio, heard):
        post(TwilioInboundView, {"Other": "x"})

        event = heard[0][1]
        assert (event.message_id, event.from_, event.to, event.body, event.media_urls, event.is_opt_out) == (
            None,
            "",
            "",
            "",
            [],
            False,
        )

    @pytest.mark.parametrize(
        "body", ["STOP", " stop ", "Unsubscribe", "quit", "STOPALL", "cancel", "END", "optout", "revoke"]
    )
    def test_an_opt_out_by_its_keyword(self, twilio, heard, body):
        post(TwilioInboundView, {"Body": body})

        assert heard[0][1].is_opt_out is True

    def test_a_message_saying_stop_among_others_isnt(self, twilio, heard):
        post(TwilioInboundView, {"Body": "please stop"})

        assert heard[0][1].is_opt_out is False

    @pytest.mark.parametrize(
        ("opt_out", "body", "is_opt_out"), [("STOP", "Arrêter", True), ("START", "STOP", False), ("HELP", "x", False)]
    )
    def test_an_opt_out_by_twilios_own_word(self, twilio, heard, opt_out, body, is_opt_out):
        post(TwilioInboundView, {"Body": body, "OptOutType": opt_out})

        assert heard[0][1].is_opt_out is is_opt_out

    def test_a_signature_that_isnt_twilios(self, twilio, heard):
        with pytest.raises(SMSWebhookValidationFailure):
            post(TwilioInboundView, {"Body": "STOP"}, sign="forged")
        assert heard == []


class TestSupersedes:
    def test_a_first_report(self):
        assert supersedes(Status.QUEUED, None)

    def test_a_later_one(self):
        assert supersedes(Status.DELIVERED, Status.SENT)
        assert supersedes(Status.SENT, Status.QUEUED)

    def test_the_same_one(self):
        assert supersedes(Status.SENT, Status.SENT)

    def test_a_late_earlier_one(self):
        assert not supersedes(Status.SENT, Status.DELIVERED)
        assert not supersedes(Status.QUEUED, Status.FAILED)

    def test_every_status_is_ranked(self):
        assert set(RANKS) == set(Status)
        assert [RANKS[status] for status in Status] == [0, 0, 1, 2, 2, 2, 2, 1]


class TestAHashedBody:
    def test_signed_with_no_parameters_whatever_it_holds(self, twilio, heard):
        body = "MessageSid=SM1&MessageStatus=sent"
        digest = hashlib.sha256(body.encode()).hexdigest()
        request = RequestFactory().post(
            f"/sms/status/?bodySHA256={digest}", body, content_type="application/x-www-form-urlencoded"
        )
        request.META["HTTP_X_TWILIO_SIGNATURE"] = expected("token", f"{URL}?bodySHA256={digest}", {})

        assert TwilioStatusView.as_view()(request).status_code == 204
