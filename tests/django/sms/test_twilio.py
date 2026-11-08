"""The Twilio sender - over httpx.MockTransport, never Twilio itself - and the failover sender."""

import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from isik.django import sms
from isik.django.sms import (
    RecipientStatus,
    SMSAPIError,
    SMSMessage,
    SMSRateLimited,
    SMSRecipientsRefused,
    SMSUnsupportedFeature,
    Status,
    senders,
)
from isik.django.sms.backends import failover, twilio
from isik.django.sms.backends.locmem import respond


NUMBER = "+14155550123"
OTHER = "+447700900123"
URL = "https://api.twilio.com/2010-04-01/Accounts/ACtest/Messages.json"
TWILIO = "isik.django.sms.backends.twilio.SMSBackend"
LOCMEM = "isik.django.sms.backends.locmem.SMSBackend"


class Twilio:
    """A fake Twilio: answers each request as scripted, recording what it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else (201, {"sid": "SM1", "status": "queued"})
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        status, body = answer
        return httpx.Response(status, json=body)

    def form(self, index=0):
        return parse_qs(self.requests[index].content.decode(), keep_blank_values=True)


def sender(fake, **options):
    return twilio.SMSBackend(
        **{"account_sid": "ACtest", "auth_token": "token", "from_": "+14155550100", **options},
        transport=httpx.MockTransport(fake),
    )


def send(fake, message, **options):
    backend = sender(fake, **options)
    backend.send_messages([message])
    return message.send_status


@pytest.fixture(autouse=True)
def no_way_out(monkeypatch):
    # A client that read the environment would go through this proxy, which refuses every connection.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")


class TestTheTransport:
    def test_kept(self):
        transport = httpx.MockTransport(Twilio())

        assert twilio.SMSBackend(account_sid="AC", transport=transport).transport is transport

    def test_the_client_goes_through_it_and_never_the_environments(self):
        transport = httpx.MockTransport(Twilio())
        backend = twilio.SMSBackend(account_sid="AC", auth_token="t", transport=transport)

        backend.open()
        try:
            assert backend.client._transport is transport
            assert backend.client._mounts == {}
            assert backend.client.trust_env is False
        finally:
            backend.close()


class TestTheRequest:
    def test_posts_the_message_signed_with_the_auth_token(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Your code is 123456"))

        (request,) = fake.requests
        assert (request.method, str(request.url)) == ("POST", URL)
        assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"ACtest:token").decode()
        assert fake.form() == {"To": [NUMBER], "Body": ["Your code is 123456"], "From": ["+14155550100"]}

    def test_signed_with_an_api_key(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi"), auth_token=None, api_key_sid="SKkey", api_key_secret="secret")

        assert fake.requests[0].headers["Authorization"] == "Basic " + base64.b64encode(b"SKkey:secret").decode()
        assert str(fake.requests[0].url) == URL

    def test_one_request_a_number(self):
        fake = Twilio((201, {"sid": "SM1", "status": "queued"}), (201, {"sid": "SM2", "status": "queued"}))

        status = send(fake, SMSMessage([NUMBER, OTHER], "Hi"))

        assert [fake.form(index)["To"] for index in (0, 1)] == [[NUMBER], [OTHER]]
        assert status.message_ids == {"SM1", "SM2"}

    def test_from_a_messaging_service(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi"), from_=None, messaging_service_sid="MGservice")

        assert fake.form()["MessagingServiceSid"] == ["MGservice"]
        assert "From" not in fake.form()

    def test_a_message_naming_its_sender_over_the_messaging_service(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi", "Acme"), messaging_service_sid="MGservice")

        assert fake.form()["From"] == ["Acme"]
        assert "MessagingServiceSid" not in fake.form()

    def test_the_status_callback_carries_the_metadata(self):
        fake = Twilio()

        send(
            fake,
            SMSMessage(NUMBER, "Hi", metadata={"invite": "7", "kind": "a b"}),
            status_callback="https://acme/cb?x=1",
        )

        assert fake.form()["StatusCallback"] == ["https://acme/cb?x=1&invite=7&kind=a+b"]

    def test_the_messages_own_callback(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi", status_callback="https://acme/cb"), status_callback="https://other")

        assert fake.form()["StatusCallback"] == ["https://acme/cb"]

    def test_metadata_without_a_callback_is_unsupported(self):
        with pytest.raises(SMSUnsupportedFeature) as refused:
            send(Twilio(), SMSMessage(NUMBER, "Hi", metadata={"a": "1"}))

        assert str(refused.value) == (
            "SMSBackend can't send a message's metadata without a status_callback - set ignore_unsupported_features "
            "to send it without."
        )

    def test_metadata_without_a_callback_ignored(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi", metadata={"a": "1"}), ignore_unsupported_features=True)

        assert "StatusCallback" not in fake.form()

    def test_media_and_validity(self):
        fake = Twilio()

        send(
            fake,
            SMSMessage(NUMBER, "Hi", media_urls=["https://x/1.png", "https://x/2.png"], validity=timedelta(hours=1)),
        )

        assert fake.form()["MediaUrl"] == ["https://x/1.png", "https://x/2.png"]
        assert fake.form()["ValidityPeriod"] == ["3600"]

    def test_scheduled_through_a_messaging_service(self):
        fake = Twilio((201, {"sid": "SM1", "status": "scheduled"}))

        status = send(
            fake,
            SMSMessage(NUMBER, "Hi", send_at=datetime(2030, 1, 1, 9, tzinfo=UTC)),
            from_=None,
            messaging_service_sid="MGservice",
        )

        assert (fake.form()["SendAt"], fake.form()["ScheduleType"]) == (["2030-01-01T09:00:00+00:00"], ["fixed"])
        assert status.recipients[NUMBER].status is Status.SCHEDULED

    def test_scheduling_without_a_messaging_service_is_unsupported(self):
        with pytest.raises(SMSUnsupportedFeature) as refused:
            send(Twilio(), SMSMessage(NUMBER, "Hi", send_at=datetime(2030, 1, 1, tzinfo=UTC)))

        assert str(refused.value) == (
            "SMSBackend can't send a message's send_at without a messaging_service_sid - set "
            "ignore_unsupported_features to send it without."
        )

    def test_scheduling_without_a_messaging_service_ignored(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi", send_at=datetime(2030, 1, 1, tzinfo=UTC)), ignore_unsupported_features=True)

        assert "SendAt" not in fake.form()

    def test_message_type_is_unsupported(self):
        with pytest.raises(SMSUnsupportedFeature, match="message_type"):
            send(Twilio(), SMSMessage(NUMBER, "Hi", message_type="transactional"))

    def test_provider_extra_goes_last(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi", provider_extra={"SmartEncoded": "true", "Body": "Over"}))

        assert (fake.form()["SmartEncoded"], fake.form()["Body"]) == (["true"], ["Over"])

    def test_a_region(self):
        fake = Twilio()

        send(fake, SMSMessage(NUMBER, "Hi"), region="ie1", edge="dublin")

        assert str(fake.requests[0].url) == "https://api.dublin.ie1.twilio.com/2010-04-01/Accounts/ACtest/Messages.json"


class TestTheAnswer:
    def test_a_message_twilio_took(self):
        body = {
            "sid": "SM1",
            "status": "accepted",
            "num_segments": "2",
            "price": "-0.0158",
            "price_unit": "USD",
            "error_code": None,
            "error_message": None,
        }

        status = send(Twilio((201, body)), SMSMessage(NUMBER, "Hi"))

        assert status.recipients[NUMBER] == RecipientStatus(
            Status.QUEUED, message_id="SM1", segments=2, price="-0.0158", price_unit="USD", raw=body
        )

    @pytest.mark.parametrize(
        ("said", "status"),
        [
            ("queued", Status.QUEUED),
            ("scheduled", Status.SCHEDULED),
            ("sending", Status.SENT),
            ("sent", Status.SENT),
            ("delivered", Status.DELIVERED),
            ("undelivered", Status.FAILED),
            ("failed", Status.FAILED),
            ("receiving", Status.UNKNOWN),
        ],
    )
    def test_its_status(self, said, status):
        answer = send(Twilio((201, {"sid": "SM1", "status": said})), SMSMessage(NUMBER, "Hi"))

        assert answer.recipients[NUMBER].status is status

    def test_an_error_code_on_a_message_it_took(self):
        answer = send(
            Twilio((201, {"sid": "SM1", "status": "failed", "error_code": 30007, "error_message": "Filtered"})),
            SMSMessage(NUMBER, "Hi"),
        )

        assert (answer.recipients[NUMBER].error_code, answer.recipients[NUMBER].error_message) == ("30007", "Filtered")

    def test_an_answer_that_isnt_json(self):
        answer = send(Twilio(httpx.Response(201, text="ok")), SMSMessage(NUMBER, "Hi"))

        assert answer.recipients[NUMBER] == RecipientStatus(Status.UNKNOWN, raw={})

    @pytest.mark.parametrize(
        ("code", "status"),
        [
            (21211, Status.INVALID),
            (21614, Status.INVALID),
            (21612, Status.INVALID),
            (21610, Status.REJECTED),
            (21408, Status.REJECTED),
        ],
    )
    def test_a_number_it_refuses(self, code, status):
        body = {"code": code, "message": "Nope", "more_info": "https://www.twilio.com/docs/errors", "status": 400}

        answer = send(
            Twilio((400, body), (201, {"sid": "SM2", "status": "queued"})),
            SMSMessage([NUMBER, OTHER], "Hi"),
        )

        assert answer.recipients[NUMBER] == RecipientStatus(
            status, error_code=str(code), error_message="Nope", raw=body
        )
        assert answer.recipients[OTHER].status is Status.QUEUED

    def test_every_number_refused(self):
        with pytest.raises(SMSRecipientsRefused):
            send(Twilio((400, {"code": 21610, "message": "Unsubscribed"})), SMSMessage(NUMBER, "Hi"))

    def test_rate_limited(self):
        fake = Twilio(
            httpx.Response(429, json={"code": 20429, "message": "Too Many Requests"}, headers={"Retry-After": "2"})
        )

        with pytest.raises(SMSRateLimited) as raised:
            send(fake, SMSMessage(NUMBER, "Hi"))

        error = raised.value
        # Twilio documents no Retry-After, and one it sends anyway isn't taken as a promise.
        assert (error.retry_after, error.provider_code, error.status_code, error.retryable) == (None, 20429, 429, True)
        assert error.response == {"code": 20429, "message": "Too Many Requests"}
        assert str(error) == "Twilio refused the message (429, code 20429): Too Many Requests"
        assert len(fake.requests) == 1

    def test_rate_limited_by_code_alone(self):
        with pytest.raises(SMSRateLimited):
            send(Twilio((400, {"code": 20429, "message": "Slow"})), SMSMessage(NUMBER, "Hi"))

    def test_rate_limited_by_status_alone(self):
        with pytest.raises(SMSRateLimited) as raised:
            send(Twilio(httpx.Response(429, text="slow down")), SMSMessage(NUMBER, "Hi"))

        assert (raised.value.provider_code, raised.value.response) == (None, {})

    def test_a_430_isnt_rate_limited(self):
        with pytest.raises(SMSAPIError) as raised:
            send(Twilio(httpx.Response(430, text="x")), SMSMessage(NUMBER, "Hi"))

        assert type(raised.value) is SMSAPIError

    def test_another_refusal(self):
        with pytest.raises(SMSAPIError) as raised:
            send(Twilio((400, {"code": 21606, "message": "The From number is not valid"})), SMSMessage(NUMBER, "Hi"))

        error = raised.value
        assert type(error) is SMSAPIError
        assert (error.provider_code, error.status_code, error.retryable) == (21606, 400, False)
        assert error.response == {"code": 21606, "message": "The From number is not valid"}
        assert str(error) == "Twilio refused the message (400, code 21606): The From number is not valid"

    def test_a_failure_on_its_side_is_retryable(self):
        with pytest.raises(SMSAPIError) as raised:
            send(Twilio(httpx.Response(503, text="down")), SMSMessage(NUMBER, "Hi"))

        assert raised.value.retryable
        assert str(raised.value) == "Twilio refused the message (503, code None): Service Unavailable"

    def test_a_500_is_retryable(self):
        with pytest.raises(SMSAPIError) as raised:
            send(Twilio((500, {"code": 20500, "message": "Internal"})), SMSMessage(NUMBER, "Hi"))

        assert raised.value.retryable

    def test_a_499_isnt(self):
        with pytest.raises(SMSAPIError) as raised:
            send(Twilio((499, {"code": 1, "message": "x"})), SMSMessage(NUMBER, "Hi"))

        assert not raised.value.retryable


class TestReachingIt:
    @pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")])
    def test_a_request_that_never_reached_it_is_tried_again(self, failure):
        fake = Twilio(failure, failure, (201, {"sid": "SM1", "status": "queued"}))

        status = send(fake, SMSMessage(NUMBER, "Hi"))

        assert len(fake.requests) == 3
        assert status.message_ids == {"SM1"}

    def test_until_its_out_of_retries(self):
        fake = Twilio(httpx.ConnectError("refused"), httpx.ConnectError("refused"))

        with pytest.raises(SMSAPIError) as raised:
            send(fake, SMSMessage(NUMBER, "Hi"), retries=1)

        assert len(fake.requests) == 2
        assert raised.value.retryable
        assert str(raised.value) == "Twilio couldn't be reached: refused"
        assert isinstance(raised.value.__cause__, httpx.ConnectError)

    def test_each_retry_counts(self):
        fake = Twilio(*[httpx.ConnectError("refused")] * 3)

        with pytest.raises(SMSAPIError):
            send(fake, SMSMessage(NUMBER, "Hi"), retries=2)

        assert len(fake.requests) == 3

    def test_no_retries(self):
        fake = Twilio(httpx.ConnectError("refused"))

        with pytest.raises(SMSAPIError):
            send(fake, SMSMessage(NUMBER, "Hi"), retries=0)

        assert len(fake.requests) == 1

    def test_a_request_that_may_have_reached_it_isnt(self):
        fake = Twilio(httpx.ReadTimeout("slow"))

        with pytest.raises(SMSAPIError) as raised:
            send(fake, SMSMessage(NUMBER, "Hi"))

        assert len(fake.requests) == 1
        assert not raised.value.retryable
        assert str(raised.value) == (
            "Twilio may or may not have the message - the request failed after it was sent: slow"
        )


class TestTheClient:
    def test_one_client_for_every_message(self):
        backend = sender(Twilio())

        with backend:
            client = backend.client
            backend.send_messages([SMSMessage(NUMBER, "a"), SMSMessage(NUMBER, "b")])
            assert backend.client is client

        assert backend.client is None

    def test_open_twice_opens_once(self):
        backend = sender(Twilio())

        assert backend.open() is True
        assert backend.open() is False
        backend.close()
        backend.close()

    def test_send_one_opens_what_it_needs(self):
        backend = sender(Twilio())

        assert backend.send_one(SMSMessage(NUMBER, "Hi"), NUMBER).message_id == "SM1"
        assert backend.client is None

    def test_send_one_leaves_open_what_was(self):
        backend = sender(Twilio())
        backend.open()

        backend.send_one(SMSMessage(NUMBER, "Hi"), NUMBER)

        assert backend.client is not None
        backend.close()

    def test_its_client(self):
        backend = twilio.SMSBackend(account_sid="AC", auth_token="t", timeout=3)

        backend.open()
        try:
            assert backend.client.timeout == httpx.Timeout(3)
            assert backend.client.trust_env is False
            assert backend.client.auth._auth_header == "Basic " + base64.b64encode(b"AC:t").decode()
        finally:
            backend.close()

    def test_its_defaults(self):
        backend = twilio.SMSBackend(account_sid="AC")

        assert (backend.timeout, backend.retries, backend.webhook_auth_tokens, backend.webhook_url) == (10, 2, (), None)
        assert (backend.region, backend.edge, backend.messaging_service_sid, backend.transport) == (None,) * 4
        assert backend.base_url == "https://api.twilio.com"

    def test_webhook_tokens(self):
        assert twilio.SMSBackend(account_sid="AC", auth_token="a", webhook_auth_tokens=["b"]).webhook_tokens() == (
            "a",
            "b",
        )
        assert twilio.SMSBackend(account_sid="AC", api_key_sid="SK", api_key_secret="s").webhook_tokens() == ()

    def test_with_metadata(self):
        assert twilio.with_metadata("https://acme/cb", {}) == "https://acme/cb"
        assert twilio.with_metadata("https://acme/cb", {"a": "1"}) == "https://acme/cb?a=1"


class TestItsCheck:
    def test_configured(self):
        assert twilio.SMSBackend.check({"account_sid": "AC", "auth_token": "t", "from_": "+1"}) == []
        assert (
            twilio.SMSBackend.check(
                {
                    "account_sid": "AC",
                    "api_key_sid": "SK",
                    "api_key_secret": "s",
                    "messaging_service_sid": "MG",
                    "region": "ie1",
                    "edge": "dublin",
                }
            )
            == []
        )

    def test_nothing_set(self):
        assert twilio.SMSBackend.check({}) == [
            "twilio.SMSBackend sends from account_sid - set it.",
            "twilio.SMSBackend signs in with auth_token, or api_key_sid and api_key_secret - set them.",
            "twilio.SMSBackend sends from from_, or a messaging_service_sid - set one.",
        ]

    def test_half_an_api_key(self):
        assert twilio.SMSBackend.check({"account_sid": "AC", "api_key_sid": "SK", "from_": "+1"}) == [
            "twilio.SMSBackend signs in with auth_token, or api_key_sid and api_key_secret - set them."
        ]

    @pytest.mark.parametrize("given", [{"region": "ie1"}, {"edge": "dublin"}])
    def test_a_region_without_an_edge(self, given):
        assert twilio.SMSBackend.check({"account_sid": "AC", "auth_token": "t", "from_": "+1", **given}) == [
            "twilio.SMSBackend takes region and edge together - set both, or neither."
        ]


class TestFailover:
    @pytest.fixture
    def configured(self, senders_configured):
        senders_configured(
            {
                "default": {
                    "BACKEND": "isik.django.sms.backends.failover.SMSBackend",
                    "OPTIONS": {"senders": ["first", "second"]},
                },
                "first": {"BACKEND": LOCMEM},
                "second": {"BACKEND": LOCMEM},
            }
        )

    def backend(self, *answers):
        first, second = (
            Scripted(answer, alias=alias) for answer, alias in zip(answers, ("first", "second"), strict=True)
        )
        backend = failover.SMSBackend.__new__(failover.SMSBackend)
        failover.BaseSMSBackend.__init__(backend)
        backend.senders = [first, second]
        return backend, first, second

    def test_builds_the_senders_it_names(self, configured):
        backend = senders.default

        assert [sender.alias for sender in backend.senders] == ["first", "second"]

    def test_the_first_sends(self, configured):
        assert sms.send_sms(NUMBER, "Hi").recipients[NUMBER].status is Status.QUEUED

    @pytest.mark.parametrize(
        "failure",
        [SMSAPIError("down", retryable=True), SMSRateLimited("slow")],
    )
    def test_the_next_one_when_one_couldnt_send(self, failure):
        backend, first, second = self.backend(failure, Status.QUEUED)

        assert backend.send_messages([SMSMessage(NUMBER, "Hi")]) == 1
        assert (first.sent, second.sent) == ([("Hi", NUMBER)], [("Hi", NUMBER)])

    def test_not_when_one_refused(self):
        backend, first, second = self.backend(SMSAPIError("bad from"), Status.QUEUED)

        with pytest.raises(SMSAPIError, match="bad from"):
            backend.send_messages([SMSMessage(NUMBER, "Hi")])
        assert second.sent == []

    def test_not_when_a_number_was_refused(self):
        backend, first, second = self.backend(Status.REJECTED, Status.QUEUED)

        with pytest.raises(SMSRecipientsRefused):
            backend.send_messages([SMSMessage(NUMBER, "Hi")])
        assert second.sent == []

    def test_the_last_ones_failure_is_raised(self):
        backend, first, second = self.backend(
            SMSAPIError("down", retryable=True), SMSAPIError("also down", retryable=True)
        )

        with pytest.raises(SMSAPIError, match="also down"):
            backend.send_messages([SMSMessage(NUMBER, "Hi")])
        assert second.sent == [("Hi", NUMBER)]

    def test_each_sender_refuses_what_it_cant_send(self):
        backend, first, second = self.backend(Status.QUEUED, Status.QUEUED)
        first.features = frozenset()

        with pytest.raises(SMSUnsupportedFeature):
            backend.send_messages([SMSMessage(NUMBER, "Hi", validity=60)])

    def test_the_next_sender_may_send_what_the_first_cant(self):
        backend, first, second = self.backend(SMSAPIError("down", retryable=True), Status.QUEUED)
        first.features = frozenset({"validity"})
        second.features = frozenset({"validity"})

        assert backend.send_messages([SMSMessage(NUMBER, "Hi", validity=60)]) == 1

    def test_opens_and_closes_the_ones_it_opened(self):
        backend, first, second = self.backend(Status.QUEUED, Status.QUEUED)
        second.opens = False

        assert backend.open() is True
        backend.close()
        backend.close()

        assert (first.opened, first.closed, second.opened, second.closed) == (1, 1, 1, 0)

    def test_opens_nothing_when_none_open(self):
        backend, first, second = self.backend(Status.QUEUED, Status.QUEUED)
        first.opens = second.opens = False

        assert backend.open() is False

    def test_close_before_open(self):
        backend, first, second = self.backend(Status.QUEUED, Status.QUEUED)

        backend.close()

        assert first.closed == 0

    def test_its_check(self):
        assert failover.SMSBackend.check({"senders": ["a", "b"]}) == []
        message = "failover.SMSBackend tries senders, a list of the aliases of other senders - set it."
        assert failover.SMSBackend.check({}) == [message]
        assert failover.SMSBackend.check({"senders": "a"}) == [message]
        assert failover.SMSBackend.check({"senders": []}) == [message]
        assert not failover.SMSBackend.sends_nowhere

    def test_answers_what_the_sender_it_went_through_did(self, configured):
        respond(NUMBER, Status.SENT)

        assert sms.send_sms(NUMBER, "Hi").recipients[NUMBER].status is Status.SENT


class Scripted(failover.BaseSMSBackend):
    features = frozenset({"validity"})

    def __init__(self, answer, **options):
        super().__init__(**options)
        self.answer = answer
        self.sent = []
        self.opens = True
        self.opened = self.closed = 0

    def open(self):
        self.opened += 1
        return self.opens

    def close(self):
        self.closed += 1

    def send_one(self, message, recipient):
        self.sent.append((message.body, recipient))
        if isinstance(self.answer, Exception):
            raise self.answer
        return RecipientStatus(self.answer)
