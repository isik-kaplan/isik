"""
The push channel against a fake APNs, FCM and Google token endpoint: what each request carries, how each
answer is read, which answers deactivate a device and which leave it be, and the credentials kept between
pushes.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.utils import timezone, translation

from isik.django.apps.notifications import pipeline
from isik.django.apps.notifications.audience.helpers import forget_devices, register_device, subscribe
from isik.django.apps.notifications.audience.models import Device, InboxItem
from isik.django.apps.notifications.channels import push
from isik.django.apps.notifications.choices import Platform, PushEnvironment
from isik.django.apps.notifications.kinds import PushContent, PushDataField, raise_notice
from isik.django.apps.notifications.models import Delivery, DeliveryAttempt, DeliveryState, PushDelivery
from isik.django.apps.notifications.pipeline import RetryRefused, retry_delivery
from tests.django.apps.notifications.test_pipeline import fan_out, redeclared
from tests.testapp.models import EmailUser


pytestmark = pytest.mark.django_db

_EC = ec.generate_private_key(ec.SECP256R1())
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


APNS = {"TEAM_ID": "TEAM123", "KEY_ID": "KEY456", "KEY": _pem(_EC), "TOPIC": "com.acme.app"}
ACCOUNT = {
    "client_email": "push@acme.iam.example",
    "private_key": _pem(_RSA),
    "project_id": "acme-app",
    "token_uri": "https://oauth.example/token",
}
TOKEN = "a" * 64
NOW = 1_800_000_000


class Services:
    """APNs, FCM and Google's token endpoint, faked: every request kept, each answered from `answers` or by default."""

    def __init__(self):
        self.requests, self.answers = [], []

    def __call__(self, request):
        self.requests.append(request)
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        if request.url.host == "oauth.example":
            return httpx.Response(200, json={"access_token": "ya29.first", "expires_in": 3600})
        return httpx.Response(200, json={"name": "projects/acme-app/messages/1"})

    def sent(self):
        """The requests to APNs or FCM, leaving out token exchanges."""
        return [request for request in self.requests if request.url.host != "oauth.example"]


@pytest.fixture
def services(monkeypatch, settings):
    """The push channel on, with both services' credentials, talking to fakes - at a clock of NOW."""
    faked = Services()
    monkeypatch.setattr(push, "client", lambda: httpx.Client(transport=httpx.MockTransport(faked)))
    monkeypatch.setattr(push, "_tokens", {})
    monkeypatch.setattr(push.time, "time", lambda: NOW)
    settings.ISIK_NOTIFICATIONS = {
        **settings.ISIK_NOTIFICATIONS,
        "CHANNELS": ("inbox", "email", "webhook", "push"),
        "PUSH": {"APNS": APNS, "FCM": {"CREDENTIALS": ACCOUNT}},
        "RECORD_ATTEMPTS": "all",
    }
    return faked


@pytest.fixture
def phone(alice):
    return register_device(alice, Platform.APNS, TOKEN)


@pytest.fixture
def android(alice):
    return register_device(alice, Platform.FCM, "fcm-token")


@pytest.fixture
def remind(alice, services, dispatched, django_capture_on_commit_callbacks):
    """Raises a daily reminder for alice, subscribed to it - returning its push delivery to `device`."""
    subscribe(alice, "parcel_reminder")

    def raised(device, snapshot=None):
        notice = raise_notice("parcel_reminder", variant="daily", snapshot=snapshot or {"name": "Bob's parcel"})
        fan_out(django_capture_on_commit_callbacks)
        return Delivery.objects.get(notice=notice, channel="push", push__device_id=str(device.pk))

    return raised


@contextmanager
def pushing(monkeypatch, **content):
    with redeclared(monkeypatch, "parcel_reminder", push=PushContent(**content)):
        yield


def claims(token, key):
    unchecked = {"verify_aud": False, "verify_exp": False, "verify_iat": False}
    return jwt.decode(token, key, algorithms=["ES256", "RS256"], options=unchecked)


class TestWhoItReaches:
    def test_each_active_device_of_each_user_once(
        self, alice, bob, services, dispatched, django_capture_on_commit_callbacks
    ):
        first, second = register_device(alice, Platform.APNS, "one"), register_device(alice, Platform.FCM, "two")
        register_device(alice, Platform.APNS, "dead")
        Device.objects.filter(token="dead").update(is_active=False)
        register_device(bob, Platform.APNS, "bobs")
        subscribe(alice, "parcel_reminder")

        notice = raise_notice("parcel_reminder", variant="daily", snapshot={"name": "x"})
        fan_out(django_capture_on_commit_callbacks)

        reached = Delivery.objects.filter(notice=notice, channel="push").order_by("target_key")
        assert [delivery.target_key for delivery in reached] == sorted([f"device:{first.pk}", f"device:{second.pk}"])
        assert sorted(PushDelivery.objects.values_list("device_id", "user_id")) == sorted(
            [(str(first.pk), str(alice.pk)), (str(second.pk), str(alice.pk))]
        )


class TestApns:
    def test_what_it_sends(self, remind, phone, services):
        delivery = remind(phone)

        assert pipeline.send(delivery.pk).state == DeliveryState.SENT

        (request,) = services.sent()
        assert str(request.url) == f"https://api.push.apple.com/3/device/{TOKEN}"
        assert request.method == "POST"
        assert {name: request.headers[name] for name in ("apns-topic", "apns-push-type", "apns-priority")} == {
            "apns-topic": "com.acme.app",
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        assert request.headers["apns-id"] == str(delivery.pk)
        assert "apns-collapse-id" not in request.headers
        assert "apns-expiration" not in request.headers
        assert json.loads(request.content) == {
            "aps": {
                "alert": {"title": "Daily reminder", "body": "Your daily reminder: Bob's parcel."},
                "sound": "default",
            },
            "notice": str(delivery.notice_id),
            "kind": "parcel_reminder",
            "variant": "daily",
        }

    def test_its_provider_token(self, remind, phone, services):
        pipeline.send(remind(phone).pk)

        scheme, token = services.sent()[0].headers["authorization"].split(" ")
        assert scheme == "bearer"
        assert jwt.get_unverified_header(token) == {"alg": "ES256", "kid": "KEY456", "typ": "JWT"}
        assert claims(token, _EC.public_key()) == {"iss": "TEAM123", "iat": NOW}

    def test_the_attempt_keeps_no_credentials(self, remind, phone, services):
        delivery = remind(phone)
        pipeline.send(delivery.pk)

        (request,) = services.sent()
        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert attempt.request == {
            "method": "POST",
            "url": f"https://api.push.apple.com/3/device/{TOKEN}",
            "headers": {
                "apns-topic": "com.acme.app",
                "apns-push-type": "alert",
                "apns-priority": "10",
                "apns-id": str(delivery.pk),
            },
            "body": json.loads(request.content),
        }
        assert attempt.response == {
            "headers": {"content-length": "39", "content-type": "application/json"},
            "body": '{"name":"projects/acme-app/messages/1"}',
        }
        assert attempt.status_code == 200
        assert [name for name, _value in request.headers.raw if name.startswith(b"a")] == [
            b"apns-topic",
            b"apns-push-type",
            b"apns-priority",
            b"apns-id",
            b"authorization",
        ]

    def test_a_development_build_goes_to_the_sandbox_under_its_own_topic(self, remind, alice, services):
        device = register_device(
            alice, Platform.APNS, TOKEN, environment=PushEnvironment.SANDBOX, app_id="com.acme.beta"
        )

        pipeline.send(remind(device).pk)

        (request,) = services.sent()
        assert str(request.url) == f"https://api.sandbox.push.apple.com/3/device/{TOKEN}"
        assert request.headers["apns-topic"] == "com.acme.beta"

    def test_one_token_serves_until_its_lifetime_is_up(self, remind, phone, services, monkeypatch):
        for _ in range(2):
            pipeline.send(remind(phone).pk)
        monkeypatch.setattr(push.time, "time", lambda: NOW + push.APNS_TOKEN_LIFETIME)
        pipeline.send(remind(phone).pk)

        tokens = [request.headers["authorization"] for request in services.sent()]
        assert tokens[0] == tokens[1] != tokens[2]
        assert claims(tokens[2].split(" ")[1], _EC.public_key())["iat"] == NOW + push.APNS_TOKEN_LIFETIME

    @pytest.mark.parametrize(
        ("status", "reason"),
        [(410, "Unregistered"), (410, "ExpiredToken"), (400, "BadDeviceToken"), (400, "DeviceTokenNotForTopic")],
    )
    def test_a_dead_token_deactivates_the_device(self, remind, phone, services, status, reason):
        delivery = remind(phone)
        services.answers.append(httpx.Response(status, json={"reason": reason}))

        assert pipeline.send(delivery.pk).state == DeliveryState.ABANDONED

        phone.refresh_from_db()
        assert phone.is_active is False
        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert attempt.error == f"APNs says the token is dead ({reason}) - the device is deactivated."
        assert (attempt.status_code, json.loads(attempt.response["body"])) == (status, {"reason": reason})

    def test_a_refusal_of_the_push_leaves_the_device_be(self, remind, phone, services):
        delivery = remind(phone)
        services.answers.append(httpx.Response(413, json={"reason": "PayloadTooLarge"}))

        assert pipeline.send(delivery.pk).state == DeliveryState.ABANDONED

        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert (attempt.error, attempt.status_code) == ("APNs answered 413 (PayloadTooLarge).", 413)
        phone.refresh_from_db()
        assert phone.is_active is True

    def test_a_refusal_of_the_credentials_leaves_every_device_be(self, remind, phone, services):
        delivery = remind(phone)
        services.answers.append(httpx.Response(403, json={"reason": "InvalidProviderToken"}))

        assert pipeline.send(delivery.pk).state == DeliveryState.ABANDONED

        phone.refresh_from_db()
        assert phone.is_active is True

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_a_busy_service_is_tried_again(self, remind, phone, services, status):
        delivery = remind(phone)
        services.answers.append(httpx.Response(status, json={"reason": "TooManyRequests"}))

        assert pipeline.send(delivery.pk).state == DeliveryState.PENDING

    def test_an_answer_with_no_body_is_read_as_no_reason(self, remind, phone, services):
        delivery = remind(phone)
        services.answers.append(httpx.Response(500, text="oops"))

        pipeline.send(delivery.pk)

        assert DeliveryAttempt.objects.get(delivery=delivery).error == "APNs answered 500 ()."

    def test_an_expired_provider_token_is_made_again_on_the_next_try(self, remind, phone, services, monkeypatch):
        delivery = remind(phone)
        services.answers.append(httpx.Response(403, json={"reason": "ExpiredProviderToken"}))

        assert pipeline.send(delivery.pk).state == DeliveryState.PENDING
        assert push._tokens == {}
        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert (attempt.error, attempt.status_code) == ("APNs found the provider token expired.", 403)

    def test_a_connection_that_fails_is_tried_again(self, remind, phone, services):
        delivery = remind(phone)
        services.answers.append(httpx.ConnectError("refused"))

        assert pipeline.send(delivery.pk).state == DeliveryState.PENDING
        assert DeliveryAttempt.objects.get(delivery=delivery).error == "ConnectError: refused"


class TestFcm:
    def test_what_it_sends(self, remind, android, services):
        delivery = remind(android)

        assert pipeline.send(delivery.pk).state == DeliveryState.SENT

        (exchange, request) = services.requests
        assert str(request.url) == "https://fcm.googleapis.com/v1/projects/acme-app/messages:send"
        assert request.headers["authorization"] == "Bearer ya29.first"
        assert json.loads(request.content) == {
            "message": {
                "token": "fcm-token",
                "notification": {"title": "Daily reminder", "body": "Your daily reminder: Bob's parcel."},
                "data": {"notice": str(delivery.notice_id), "kind": "parcel_reminder", "variant": "daily"},
                "android": {"priority": "high"},
                "apns": {"headers": {}, "payload": {"aps": {"sound": "default"}}},
            }
        }
        assert b"authorization" in [name for name, _value in request.headers.raw]
        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert attempt.request == {
            "method": "POST",
            "url": str(request.url),
            "body": json.loads(request.content),
        }
        assert (attempt.status_code, attempt.response["body"]) == (200, '{"name":"projects/acme-app/messages/1"}')

    def test_its_access_token_from_the_service_account(self, remind, android, services):
        pipeline.send(remind(android).pk)

        exchange = services.requests[0]
        assert (str(exchange.url), exchange.method) == ("https://oauth.example/token", "POST")
        form = parse_qs(exchange.content.decode())
        assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        assert claims(form["assertion"][0], _RSA.public_key()) == {
            "iss": "push@acme.iam.example",
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth.example/token",
            "iat": NOW,
            "exp": NOW + 3600,
        }

    def test_one_access_token_serves_until_shortly_before_it_expires(self, remind, android, services, monkeypatch):
        pipeline.send(remind(android).pk)
        monkeypatch.setattr(push.time, "time", lambda: NOW + 3600 - push.FCM_TOKEN_MARGIN - 1)
        pipeline.send(remind(android).pk)
        services.answers.append(httpx.Response(200, json={"access_token": "ya29.second", "expires_in": 3600}))
        monkeypatch.setattr(push.time, "time", lambda: NOW + 3600 - push.FCM_TOKEN_MARGIN)
        pipeline.send(remind(android).pk)

        assert [request.headers["authorization"] for request in services.sent()] == [
            "Bearer ya29.first",
            "Bearer ya29.first",
            "Bearer ya29.second",
        ]
        assert len(services.requests) - len(services.sent()) == 2

    def test_credentials_given_as_json_naming_their_project(self, remind, android, services, settings):
        account = {key: value for key, value in ACCOUNT.items() if key != "token_uri"}
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "PUSH": {"FCM": {"CREDENTIALS": json.dumps(account), "PROJECT_ID": "acme-other"}},
        }
        services.answers.append(httpx.Response(200, json={"access_token": "ya29.google", "expires_in": 3600}))

        pipeline.send(remind(android).pk)

        exchange, request = services.requests
        assert str(exchange.url) == "https://oauth2.googleapis.com/token"
        assert str(request.url) == "https://fcm.googleapis.com/v1/projects/acme-other/messages:send"

    @pytest.mark.parametrize(("status", "state"), [(400, DeliveryState.ABANDONED), (503, DeliveryState.PENDING)])
    def test_credentials_google_refuses(self, remind, android, services, status, state):
        delivery = remind(android)
        services.answers.append(httpx.Response(status, json={"error": "invalid_grant"}))

        assert pipeline.send(delivery.pk).state == state
        assert DeliveryAttempt.objects.get(delivery=delivery).error == (
            f"Google refused the service account's credentials ({status})."
        )
        android.refresh_from_db()
        assert android.is_active is True

    @pytest.mark.parametrize(
        "error",
        [
            {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]},
            {"status": "INVALID_ARGUMENT", "details": [{"fieldViolations": [{"field": "message.token"}]}]},
            {"details": [{"errorCode": "UNREGISTERED"}]},
        ],
    )
    def test_a_dead_token_deactivates_the_device(self, remind, android, services, error):
        delivery = remind(android)
        # The token exchange first, then the send's answer.
        services.answers[:] = [
            httpx.Response(200, json={"access_token": "ya29.first", "expires_in": 3600}),
            httpx.Response(400 if error.get("status") == "INVALID_ARGUMENT" else 404, json={"error": error}),
        ]

        assert pipeline.send(delivery.pk).state == DeliveryState.ABANDONED

        android.refresh_from_db()
        assert android.is_active is False
        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert attempt.error == f"FCM says the token is dead ({error.get('status', '')}) - the device is deactivated."
        assert (attempt.status_code, json.loads(attempt.response["body"])) == (
            400 if error.get("status") == "INVALID_ARGUMENT" else 404,
            {"error": error},
        )

    @pytest.mark.parametrize(
        ("status", "error", "state"),
        [
            (400, {"status": "INVALID_ARGUMENT", "details": [{"fieldViolations": [{"field": "message.data"}]}]}, "a"),
            (400, {"status": "INVALID_ARGUMENT"}, "a"),
            (400, {"status": "INVALID_ARGUMENT", "details": [{"errorCode": "INVALID_ARGUMENT"}]}, "a"),
            (403, {"status": "PERMISSION_DENIED", "details": [{"errorCode": "SENDER_ID_MISMATCH"}]}, "a"),
            (500, {"status": "INTERNAL"}, "p"),
            (429, {"status": "RESOURCE_EXHAUSTED", "details": [{"errorCode": "QUOTA_EXCEEDED"}]}, "p"),
        ],
    )
    def test_any_other_refusal_leaves_the_device_be(self, remind, android, services, status, error, state):
        delivery = remind(android)
        services.answers[:] = [
            httpx.Response(200, json={"access_token": "ya29.first", "expires_in": 3600}),
            httpx.Response(status, json={"error": error}),
        ]

        expected = {"a": DeliveryState.ABANDONED, "p": DeliveryState.PENDING}[state]
        assert pipeline.send(delivery.pk).state == expected
        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert attempt.error == f"FCM answered {status} ({error['status']})."
        assert (attempt.status_code, json.loads(attempt.response["body"])) == (status, {"error": error})
        android.refresh_from_db()
        assert android.is_active is True

    def test_an_answer_with_no_body_is_read_as_no_error(self, remind, android, services):
        delivery = remind(android)
        services.answers[:] = [
            httpx.Response(200, json={"access_token": "ya29.first", "expires_in": 3600}),
            httpx.Response(502, text="bad gateway"),
        ]

        pipeline.send(delivery.pk)

        assert DeliveryAttempt.objects.get(delivery=delivery).error == "FCM answered 502 ()."

    def test_an_expired_access_token_is_fetched_again_on_the_next_try(self, remind, android, services):
        delivery = remind(android)
        services.answers[:] = [
            httpx.Response(200, json={"access_token": "ya29.first", "expires_in": 3600}),
            httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}}),
        ]
        register_device(android.user, Platform.APNS, TOKEN)
        push.apns_token(APNS)

        assert pipeline.send(delivery.pk).state == DeliveryState.PENDING
        assert list(push._tokens) == [("apns", "TEAM123", "KEY456")]
        attempt = DeliveryAttempt.objects.get(delivery=delivery)
        assert (attempt.error, attempt.status_code) == ("FCM found the access token expired.", 401)


class TestWhatItSays:
    def test_in_the_recipients_language(self, remind, phone, services, settings, monkeypatch):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECIPIENT_LANGUAGE": BY_USER}
        with redeclared(monkeypatch, "parcel_reminder", text=_titled(lambda data, variant: translation.get_language())):
            pipeline.send(remind(phone).pk)

        assert _alert(services)["title"] == "de"

    def test_in_the_sites_language_for_a_user_gone(self, remind, phone, services, monkeypatch, settings):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "RECIPIENT_LANGUAGE": BY_USER}
        delivery = remind(phone)
        PushDelivery.objects.filter(delivery=delivery).update(user_id="0")
        with redeclared(monkeypatch, "parcel_reminder", text=_titled(lambda data, variant: translation.get_language())):
            pipeline.send(delivery.pk)

        assert _alert(services)["title"] == translation.get_language()

    def test_its_title_filled_with_the_data(self, remind, phone, services, monkeypatch):
        with redeclared(monkeypatch, "parcel_reminder", text=_titled("%(name)s is waiting")):
            pipeline.send(remind(phone).pk)

        assert _alert(services)["title"] == "Bob's parcel is waiting"

    def test_its_label_where_it_has_no_title(self, remind, phone, services, monkeypatch):
        from isik.django.apps.notifications import kinds

        monkeypatch.setattr(kinds, "vocabulary", [])
        with redeclared(monkeypatch, "parcel_reminder", text=_titled(None, label="Reminders")):
            pipeline.send(remind(phone).pk)

        assert _alert(services) == {"title": "Reminders", "body": "Your daily reminder: Bob's parcel."}

    def test_its_name_where_it_has_no_label_either(self, remind, phone, services, monkeypatch):
        from isik.django.apps.notifications import kinds

        monkeypatch.setattr(kinds, "vocabulary", [])
        with redeclared(monkeypatch, "parcel_reminder", text=kinds.KindText()):
            pipeline.send(remind(phone).pk)

        assert _alert(services) == {"title": "parcel_reminder"}

    def test_a_private_kind_says_only_what_kind_of_thing_happened(self, remind, phone, android, services, monkeypatch):
        with pushing(monkeypatch, private=True):
            pipeline.send(remind(phone).pk)
            pipeline.send(remind(android).pk)

        assert _alert(services) == {"title": "Parcel reminders"}
        assert json.loads(services.sent()[1].content)["message"]["notification"] == {"title": "Parcel reminders"}

    def test_the_fields_it_may_carry(self, remind, phone, services, monkeypatch):
        reason = PushDataField(reason="the parcel's id opens its page in the app, and says nothing about anyone")
        with pushing(monkeypatch, data={"name": reason, "count": reason, "missing": reason}):
            pipeline.send(remind(phone, snapshot={"name": "Bob's parcel", "count": 3, "secret": "x"}).pk)

        body = json.loads(services.sent()[0].content)
        assert {key: body[key] for key in ("name", "count")} == {"name": "Bob's parcel", "count": "3"}
        assert "missing" not in body
        assert "secret" not in body

    def test_its_subject(self, remind, alice, phone, services, django_capture_on_commit_callbacks):
        notice = raise_notice("parcel_reminder", variant="daily", subject=alice)
        fan_out(django_capture_on_commit_callbacks)
        pipeline.send(Delivery.objects.get(notice=notice, channel="push").pk)

        body = json.loads(services.sent()[0].content)
        assert (body["subject_label"], body["subject_id"]) == ("testapp.EmailUser", str(alice.pk))
        assert body["aps"]["alert"] == {"title": "Daily reminder", "body": "Your daily reminder: your parcel."}

    @pytest.mark.parametrize(
        ("collapse", "key"), [("kind", "parcel_reminder"), ("subject", "parcel_reminder:None:None")]
    )
    def test_collapsed_by_kind_or_subject(self, remind, phone, android, services, monkeypatch, collapse, key):
        digest = hashlib.sha1(key.encode()).hexdigest()
        with pushing(monkeypatch, collapse=collapse):
            pipeline.send(remind(phone).pk)
            pipeline.send(remind(android).pk)

        apple, google = services.sent()
        assert apple.headers["apns-collapse-id"] == digest
        assert b"apns-collapse-id" in [name for name, _value in apple.headers.raw]
        message = json.loads(google.content)["message"]
        assert message["android"]["collapse_key"] == digest
        assert message["apns"]["headers"]["apns-collapse-id"] == digest

    def test_kept_for_its_ttl(self, remind, phone, android, services, monkeypatch):
        with pushing(monkeypatch, ttl=timedelta(hours=1)):
            pipeline.send(remind(phone).pk)
            pipeline.send(remind(android).pk)

        apple, google = services.sent()
        assert apple.headers["apns-expiration"] == str(NOW + 3600)
        assert b"apns-expiration" in [name for name, _value in apple.headers.raw]
        message = json.loads(google.content)["message"]
        assert message["android"]["ttl"] == "3600s"
        assert message["apns"]["headers"]["apns-expiration"] == str(NOW + 3600)

    def test_the_badge_counts_the_unread(self, remind, alice, bob, phone, android, services, monkeypatch):
        for user, read_at in ((alice, None), (alice, None), (alice, timezone.now()), (bob, None)):
            InboxItem.objects.create(user=user, kind_id="parcel_reminder", delivery_id=uuid.uuid4(), read_at=read_at)
        with pushing(monkeypatch, badge=True):
            pipeline.send(remind(phone).pk)
            pipeline.send(remind(android).pk)

        apple, google = services.sent()
        assert json.loads(apple.content)["aps"]["badge"] == 2
        assert json.loads(google.content)["message"]["apns"]["payload"]["aps"]["badge"] == 2

    def test_no_badge_for_a_user_gone(self, remind, phone, services, monkeypatch):
        with pushing(monkeypatch, badge=True):
            delivery = remind(phone)
            PushDelivery.objects.filter(delivery=delivery).update(user_id="0")
            pipeline.send(delivery.pk)

        assert "badge" not in json.loads(services.sent()[0].content)["aps"]


def BY_USER(user):  # noqa: N802 - a hook, named as a setting's value
    """A RECIPIENT_LANGUAGE telling a user from none."""
    return "de" if user is not None else "fr"


def _titled(title, label="Parcel reminders"):
    from isik.django.apps.notifications.kinds import KindText

    return KindText(label=label, title=title, message="Your daily reminder: %(name)s.")


def _alert(services):
    return json.loads(services.sent()[0].content)["aps"]["alert"]


class TestADeviceGone:
    @pytest.mark.parametrize(
        ("change", "error"),
        [("forget", "The device was forgotten."), ("deactivate", "The device is inactive - its token was found dead.")],
    )
    def test_its_delivery_is_abandoned(self, remind, phone, services, change, error):
        delivery = remind(phone)
        devices = Device.objects.filter(pk=phone.pk)
        devices.delete() if change == "forget" else devices.update(is_active=False)

        assert pipeline.send(delivery.pk).state == DeliveryState.ABANDONED
        assert DeliveryAttempt.objects.get(delivery=delivery).error == error
        assert services.sent() == []

    def test_and_isnt_retried(self, remind, phone, services):
        delivery = remind(phone)
        Delivery.objects.filter(pk=delivery.pk).update(state=DeliveryState.ABANDONED)
        assert retry_delivery(delivery).state == DeliveryState.PENDING
        Delivery.objects.filter(pk=delivery.pk).update(state=DeliveryState.ABANDONED)
        Device.objects.filter(pk=phone.pk).update(is_active=False)

        with pytest.raises(RetryRefused, match="^The device is inactive - its token was found dead.$"):
            retry_delivery(delivery)

    def test_nor_once_its_partition_is_gone(self, remind, phone, services, settings):
        delivery = remind(phone)
        Delivery.objects.filter(pk=delivery.pk).update(state=DeliveryState.ABANDONED, partition="gone")
        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "PARTITIONS": lambda: ["north"],
            "IN_PARTITION": lambda key: nullcontext(),
        }

        with pytest.raises(RetryRefused, match="^The partition 'gone' no longer exists.$"):
            retry_delivery(delivery)


class TestPartitions:
    def test_its_device_and_user_are_read_in_the_deliverys(self, remind, phone, services, settings):
        delivery = remind(phone)
        entered = []

        @contextmanager
        def enter(key):
            entered.append(key)
            yield

        settings.ISIK_NOTIFICATIONS = {
            **settings.ISIK_NOTIFICATIONS,
            "PARTITIONS": lambda: ["north"],
            "IN_PARTITION": enter,
        }
        Delivery.objects.filter(pk=delivery.pk).update(partition="north")
        services.answers.append(httpx.Response(410, json={"reason": "Unregistered"}))

        pipeline.send(delivery.pk)
        Delivery.objects.filter(pk=delivery.pk).update(state=DeliveryState.ABANDONED)
        with pytest.raises(RetryRefused):
            retry_delivery(delivery)

        assert set(entered) == {"north"}
        assert len(entered) >= 4


class TestDevices:
    def test_registering_a_known_token_moves_it_and_wakes_it(self, alice, bob):
        first = register_device(alice, Platform.APNS, TOKEN)
        Device.objects.filter(pk=first.pk).update(is_active=False, last_seen_at=None)

        moved = register_device(bob, Platform.APNS, TOKEN, environment=PushEnvironment.SANDBOX, app_id="com.acme.b")

        assert moved.pk == first.pk
        moved.refresh_from_db()
        assert (moved.user, moved.environment, moved.app_id, moved.is_active) == (
            bob,
            PushEnvironment.SANDBOX,
            "com.acme.b",
            True,
        )
        assert moved.last_seen_at is not None

    def test_a_token_on_another_platform_is_another_device(self, alice):
        assert register_device(alice, Platform.APNS, "same").pk != register_device(alice, Platform.FCM, "same").pk

    def test_its_defaults(self, alice):
        device = register_device(alice, Platform.FCM, "plain")

        assert (device.environment, device.app_id) == (PushEnvironment.PRODUCTION, "")
        assert str(device) == "fcm:plain"
        assert str(register_device(alice, Platform.APNS, "0123456789abcdef")) == "apns:0123456789ab"

    def test_forgetting_a_users_devices(self, alice, bob):
        register_device(alice, Platform.APNS, "one")
        register_device(alice, Platform.FCM, "two")
        register_device(bob, Platform.APNS, "three")

        assert forget_devices(alice) == 2
        assert list(Device.objects.values_list("token", flat=True)) == ["three"]


class TestTheClient:
    def test_one_http2_client_a_worker_with_the_endpoint_timeout(self, settings, monkeypatch):
        settings.ISIK_NOTIFICATIONS = {**settings.ISIK_NOTIFICATIONS, "ENDPOINT_TIMEOUT": 7}
        # Its arguments, not a client: making a real one sets up TLS, which a forked mutation run can't.
        monkeypatch.setattr(push.httpx, "Client", lambda **made: made)

        assert push.client() == {"http2": True, "timeout": 7}
        assert push.client() is push.client()

    def test_forgetting_one_services_tokens_keeps_the_others(self, monkeypatch):
        monkeypatch.setattr(push, "_tokens", {("apns", "t", "k"): ("a", 1), ("fcm", "e"): ("b", 1)})

        push.forget_token("fcm")

        assert push._tokens == {("apns", "t", "k"): ("a", 1)}


def test_a_user_with_no_devices_gets_no_push(alice, services, dispatched, django_capture_on_commit_callbacks):
    subscribe(alice, "parcel_reminder")
    notice = raise_notice("parcel_reminder", variant="daily", snapshot={"name": "x"})
    fan_out(django_capture_on_commit_callbacks)

    assert not Delivery.objects.filter(notice=notice, channel="push").exists()
    assert EmailUser.objects.filter(pk=alice.pk).exists()
