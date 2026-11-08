# SMS

`isik.django.sms` - text messages sent through a configured sender, the way Django sends email through its
mailers. Each sender is an alias in `ISIK["DJANGO"]["SMS"]` ([ISIK](conf.md)), with its `BACKEND` and the
`OPTIONS` it's built with:

```python
ISIK = {
    "DJANGO": {
        "SMS": {
            "default": {
                "BACKEND": "isik.django.sms.backends.twilio.SMSBackend",
                "OPTIONS": {
                    "account_sid": os.environ["TWILIO_ACCOUNT_SID"],
                    "auth_token": os.environ["TWILIO_AUTH_TOKEN"],
                    "from_": "+14155550100",
                },
            },
            "marketing": {
                "BACKEND": "isik.django.sms.backends.twilio.SMSBackend",
                "OPTIONS": {..., "messaging_service_sid": "MG...", "fail_silently": True},
            },
        },
    },
}
```

isik reads no environment itself - the settings module does, as above. The Twilio sender needs the `sms`
extra: `pip install isik[sms]`.

## Sending

```python
from isik.django.sms import SMSMessage, send_sms

status = send_sms("+14155550123", "Your code is 123456")
send_sms(numbers, "Doors open at 7.", using="marketing", validity=timedelta(hours=1))

message = SMSMessage(["+14155550123", "+447700900123"], "See you there.", metadata={"invite": str(invite.id)})
message.send()
message.send_status.recipients["+14155550123"].message_id
```

A message goes to each number in `to` as a message of its own - every provider takes one number a request -
so its `send_status` is a `SendStatus` holding a `RecipientStatus` per number: the provider's `message_id`,
its `status` (a `Status`: `queued`, `scheduled`, `sent`, `delivered`, `invalid`, `rejected`, `failed`,
`unknown`), `segments`, `price` and `price_unit`, and an `error_code` and `error_message` where it refused.
`send_sms()` answers that `SendStatus`, and `message.sent_using` names the sender it went through.

A number is checked to be E.164 - a `+` and up to 15 digits - and nothing more: isik takes no phone number
library, so normalize numbers where they're entered.

| field | |
|---|---|
| `to` | a number, or several |
| `body` | the text |
| `from_` | a number, short code or alphanumeric sender id - the sender's `from_` unless given |
| `media_urls` | images and the like, for an MMS |
| `metadata` | `{str: str}`, which comes back with the provider's delivery reports |
| `send_at` | when to send it - a naive datetime is in the current time zone |
| `validity` | how long the provider keeps trying, a `timedelta` or seconds |
| `status_callback` | the URL its delivery reports go to, over the sender's |
| `message_type` | `"transactional"` or `"promotional"`, for a provider routing by it |
| `provider_extra` | merged into the provider's own request last, for what only it has |
| `using` | the sender it's sent through, by alias - `"default"` unless given; `sent_using` is the one it went through |

A field a sender can't send is refused (`SMSUnsupportedFeature`) unless its `ignore_unsupported_features`
option says to send the message without it.

## Senders

`senders["marketing"]` is a sender, built from its configuration on each lookup; `senders.default` is
`"default"`'s. A sender is a context manager, so one connection sends many messages:

```python
from isik.django.sms import senders

with senders["marketing"] as sender:
    sender.send_messages(messages)
```

Every sender takes these options, beside its own:

| option | |
|---|---|
| `fail_silently` | swallow an `SMSError` rather than raise it - a sender configured wrong still raises |
| `from_` | who a message naming no `from_` is sent from |
| `status_callback` | where delivery reports go, for a message naming none |
| `ignore_unsupported_features` | send a message without what the sender can't send, rather than refuse it |
| `ignore_recipient_status` | don't raise `SMSRecipientsRefused` when every number refused a message |

| backend (`isik.django.sms.backends.`) | sends |
|---|---|
| `console.SMSBackend` | each message as one line on stdout, its line breaks written `\n` (`stream` to write elsewhere) |
| `locmem.SMSBackend` | to `isik.django.sms.outbox`, for tests - see below |
| `dummy.SMSBackend` | nowhere |
| `filebased.SMSBackend` | each message a JSON line appended to `file_path` |
| `twilio.SMSBackend` | through Twilio's Messages API |
| `failover.SMSBackend` | through the first of `senders` - other senders' aliases - that can be reached |

A sender of your own subclasses `BaseSMSBackend` and sends one message to one number, answering its
`RecipientStatus`:

```python
from isik.django.sms.backends.base import BaseSMSBackend

class SMSBackend(BaseSMSBackend):
    options = ("api_key",)                      # what it takes beside every sender's options
    features = frozenset({"validity"})          # which optional fields it sends

    def __init__(self, *, api_key, **options):
        super().__init__(**options)
        self.api_key = api_key

    @classmethod
    def check(cls, options):                    # isik.E024 reports each message
        return [] if options.get("api_key") else ["acme.SMSBackend signs in with api_key - set it."]

    def send_one(self, message, recipient):
        ...
        return RecipientStatus(Status.QUEUED, message_id=answer["id"])
```

`open()` and `close()` hold what it reuses between messages - a connection - with `open()` answering whether
it opened anything.

### Twilio

| option | |
|---|---|
| `account_sid` | the account messages are sent from |
| `auth_token` | what requests are signed in with - and what Twilio signs its webhooks with |
| `api_key_sid`, `api_key_secret` | an API key to sign in with instead; webhooks still need the auth token |
| `messaging_service_sid` | a Messaging Service to send from, choosing its own number - scheduling needs one |
| `region`, `edge` | both or neither - `api.<edge>.<region>.twilio.com`, for an account outside the US |
| `timeout` | seconds a request may take, 10 unless set |
| `retries` | how many more times a request that never reached Twilio is tried, 2 unless set |
| `webhook_auth_tokens` | more tokens a webhook may be signed with, while the auth token is rotated |
| `webhook_url` | the URL Twilio posts webhooks to, where requests arrive at another (behind a proxy) |
| `transport` | the httpx transport requests go through - `httpx.MockTransport` in a test |

A request that may have reached Twilio is never sent again - Twilio takes no idempotency key, so a retry after
a timeout could send the message twice. A number it refuses as no phone's (21211, 21612, 21614) is
`Status.INVALID`; one that opted out (21610) or is in a region the account can't send to (21408),
`Status.REJECTED`. A rate-limited request raises `SMSRateLimited` - Twilio says no `Retry-After`, so its `retry_after` is None; any
other refusal raises `SMSAPIError`, with Twilio's `provider_code` and whether trying again can work
(`retryable`). `metadata` is added to the `status_callback` URL's query, which is how it comes back - so a
message with metadata and no callback is refused as unsupported.

### Failing over

```python
"default": {
    "BACKEND": "isik.django.sms.backends.failover.SMSBackend",
    "OPTIONS": {"senders": ["twilio", "backup"]},
},
```

The next sender is tried for a number only when one raises a retryable `SMSAPIError` - it couldn't be
reached, failed on its own side, or rate-limited the request. A number one refused, or a message it can't
send, is that sender's answer.

## Errors

`SMSError` is everything `fail_silently` swallows:

- `SMSAPIError` - the provider refused the request or couldn't be reached; `SMSRateLimited` is one.
- `SMSRecipientsRefused` - every number refused the message; its `status` says why, number by number.
- `SMSInvalidNumber` - a number that isn't E.164.
- `SMSUnsupportedFeature` - a field the sender can't send.

A sender configured wrong raises `ImproperlyConfigured` - `SenderDoesNotExist` for an alias that isn't
configured, `InvalidSender` for one that's configured wrong - and `fail_silently` never hides it.

## Signals

Each is sent by the sender's class, with `sender_alias` its alias:

- `pre_send(message)` - a receiver may change the message, or raise `SMSCancelSend` to send it nowhere.
- `post_send(message, status)`.
- `delivery(event)` - a delivery report, a `DeliveryEvent`.
- `inbound(event)` - a message sent to the project's number, an `InboundEvent`.

## Webhooks

```python
from isik.django.sms.webhooks.twilio import TwilioInboundView, TwilioStatusView

urlpatterns = [
    path("sms/twilio/status/", TwilioStatusView.as_view(using="default")),
    path("sms/twilio/inbound/", TwilioInboundView.as_view(using="default")),
]
```

Each checks Twilio's `X-Twilio-Signature` with the sender's auth token - or one of its `webhook_auth_tokens` -
and refuses a request it doesn't match with a 400. Behind a proxy, set the sender's `webhook_url` to the URL
Twilio posts to, which is the one it signs.

- `TwilioStatusView` sends `delivery` with a `DeliveryEvent`: `message_id`, `status`, `recipient`,
  `error_code`, the `metadata` the message was sent with, and Twilio's own fields as `raw`. Twilio doesn't
  promise its reports arrive in order - `supersedes(status, previous)` (`isik.django.sms.webhooks`) says
  whether a report moves a message on, so a late `sent` never undoes `delivered`.
- `TwilioInboundView` sends `inbound` with an `InboundEvent`: `from_`, `to`, `body`, `media_urls`, and
  `is_opt_out` for STOP and the like. Twilio honors an opt-out itself; the project records it. It answers
  with an empty TwiML response, so nothing is sent back.

## Segments

`segments(body)` is how a body is sent, which is what a provider bills by: GSM-7 while every character is in
its alphabet - 160 to a part, 153 to each of several, `€`, `{` and the like counting twice - else UCS-2 for
the whole body, 70 to a part and 67 to each of several.

```python
segments("Your code is 123456")   # Segments(encoding="GSM-7", units=19, count=1)
segments("Kodun 123456 🙂")       # Segments(encoding="UCS-2", units=15, count=1)
```

## In tests

isik's pytest plugin makes every configured sender the locmem one for the run, given its own options - as
Django's test runner does with email - and empties the outbox before each test. Each message sent is kept,
`send_status` and `sent_using` with it:

```python
from isik.django import sms

def test_sends_the_code(client):
    client.post("/accounts/phone/", {"phone": "+14155550123"})

    (message,) = sms.outbox
    assert message.to == ["+14155550123"]
    code = re.search(r"\d{6}", message.body)[0]
```

`respond(number, response)` (`isik.django.sms.backends.locmem`) scripts what a number answers - a `Status`, a
`RecipientStatus`, or an exception raised as a provider's would be:

```python
respond("+14155550100", Status.REJECTED)
respond("+14155550101", SMSRateLimited("Slow down", retry_after=1))
```

## Checks

- `isik.E024` - a sender configured wrong: no `BACKEND`, one that isn't a `BaseSMSBackend`, an option it
  doesn't take, or what its own `check()` refuses - Twilio's with no credentials or sender, say.
- `isik.W010` - a sender sending nothing anywhere (console, dummy, file) while DEBUG is off. A deployment
  meaning it - an end-to-end run reading codes back from the file - silences it like any check:
  `SILENCED_SYSTEM_CHECKS = ["isik.W010"]`. The locmem sender isn't warned of, nor held to
  the options it's given: it's what a test run - DEBUG off - makes every sender, with that sender's options.
