"""
Push, to people's phones: each active device of each user a notice reaches gets a delivery of its own, sent
straight to its platform's service over HTTP/2 - APNs for an iOS token, FCM for a Firebase one - with no
middleman. `PUSH` holds each service's credentials:

    "PUSH": {
        "APNS": {"TEAM_ID": "...", "KEY_ID": "...", "KEY": "<the .p8 key's PEM>", "TOPIC": "com.acme.app"},
        "FCM": {"CREDENTIALS": {<the service account's JSON, parsed>}},
    }

A push says what its kind's text says - its title, else its label, over its message - in the recipient's
language (`RECIPIENT_LANGUAGE`), and carries the notice's kind, variant and subject for the app to open: see
`PushContent` for keeping it private, the fields it may add, collapsing, expiry and the badge.

A device whose service says its token is dead - uninstalled, or issued to another app or environment - is
deactivated, and gets nothing more. A refusal of isik's credentials fails the delivery and leaves every
device be: it's the project's to fix, not the phone's.
"""

import functools
import hashlib
import json
import time
from contextlib import nullcontext
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.utils import translation

from isik._internal import check_extra
from isik._internal.translation import gettext as _
from isik.django.apps.notifications import conf
from isik.django.apps.notifications.audience.helpers import unread
from isik.django.apps.notifications.audience.swapping import get_device_model
from isik.django.apps.notifications.channels import Channel, Outcome, Target
from isik.django.apps.notifications.choices import Platform, PushEnvironment
from isik.django.apps.notifications.partitions import in_partition
from isik.django.apps.notifications.swapping import get_push_delivery_model


check_extra("push", "h2")
check_extra("push", "jwt")

import httpx  # noqa: E402
import jwt  # noqa: E402


APNS_HOSTS = {
    PushEnvironment.PRODUCTION: "https://api.push.apple.com",
    PushEnvironment.SANDBOX: "https://api.sandbox.push.apple.com",
}
# APNs refuses a token older than an hour, and one made afresh more often than every twenty minutes.
APNS_TOKEN_LIFETIME = 50 * 60
# What APNs answers for a token that will never work again.
APNS_DEAD = ("BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered")
FCM_SEND = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
# An FCM access token is used until this long before Google says it expires.
FCM_TOKEN_MARGIN = 5 * 60

# {(service, account): (token, the time.time() it's good until)} - one per worker, as the client is.
_tokens = {}


@functools.cache
def client():
    """
    The worker's one HTTP/2 client - APNs wants a connection kept and reused, not one a push. A push takes
    `ENDPOINT_TIMEOUT` seconds at most, as a webhook does.
    """
    return httpx.Client(http2=True, timeout=conf.setting("ENDPOINT_TIMEOUT"))


class Refused(Exception):
    """The service refused isik's credentials - the project's to fix. `retry` when a fresh token may do."""

    def __init__(self, message, *, retry):
        super().__init__(message)
        self.retry = retry


def _cached(key, make):
    if key not in _tokens or time.time() >= _tokens[key][1]:
        _tokens[key] = make()
    return _tokens[key][0]


def apns_token(config):
    """A provider token for APNs - made from the key, and kept for `APNS_TOKEN_LIFETIME`."""

    def make():
        now = int(time.time())
        made = jwt.encode(
            {"iss": config["TEAM_ID"], "iat": now}, config["KEY"], algorithm="ES256", headers={"kid": config["KEY_ID"]}
        )
        return made, now + APNS_TOKEN_LIFETIME

    return _cached(("apns", config["TEAM_ID"], config["KEY_ID"]), make)


def credentials(config):
    """The FCM service account's credentials - given parsed, or as their JSON."""
    given = config["CREDENTIALS"]
    return json.loads(given) if isinstance(given, str) else given


def fcm_token(config):
    """An OAuth access token for FCM, from the service account - kept until shortly before it expires."""
    account = credentials(config)

    def make():
        now = int(time.time())
        uri = account.get("token_uri", GOOGLE_TOKEN_URI)
        assertion = jwt.encode(
            {"iss": account["client_email"], "scope": FCM_SCOPE, "aud": uri, "iat": now, "exp": now + 3600},
            account["private_key"],
            algorithm="RS256",
        )
        answer = client().post(
            uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        )
        if answer.status_code != 200:
            raise Refused(
                _("Google refused the service account's credentials (%(status)s).") % {"status": answer.status_code},
                retry=retryable(answer.status_code),
            )
        token = answer.json()
        return token["access_token"], now + token["expires_in"] - FCM_TOKEN_MARGIN

    return _cached(("fcm", account["client_email"]), make)


def forget_token(service):
    """Drops the cached tokens of `service` - "apns" or "fcm" - so the next push makes a new one."""
    for key in [key for key in _tokens if key[0] == service]:
        del _tokens[key]


def retryable(status_code):
    return status_code in conf.setting("RETRYABLE_STATUSES")


@dataclass(frozen=True)
class Push:
    """One push, as the services take it: its text, its data (text to text), and how it's kept and collapsed."""

    title: str
    body: str
    data: dict
    collapse_id: str = None
    ttl: int = None
    badge: int = None


def _text(value):
    # A snapshot holds JSON already - a time or a key as the trigger wrote it - so JSON is all there is to write.
    return value if isinstance(value, str) else json.dumps(value)


def push_for(delivery, user):
    """What `delivery`'s push says, in `user`'s language (or the site's, for none), and carries."""
    from isik.django.apps.notifications.kinds import get_kind, rendered, text_of

    notice = delivery.notice
    kind = notice.kind.name
    content = get_kind(kind).push
    text, snapshot = text_of(kind), notice.snapshot or {}
    language = conf.setting("RECIPIENT_LANGUAGE")(user) if user is not None else None
    with translation.override(language) if language else nullcontext():
        label = str(text.label) if text.label is not None else kind
        if content.private:
            title, body = label, None
        else:
            title = rendered(text.title, snapshot, notice.variant) or label
            body = rendered(text.message, snapshot, notice.variant)
    data = {
        "notice": str(notice.pk),
        "kind": kind,
        "variant": notice.variant,
        "subject_label": notice.subject_label,
        "subject_id": notice.subject_id,
        **{name: snapshot.get(name) for name in content.data},
    }
    collapse = {"kind": kind, "subject": f"{kind}:{notice.subject_label}:{notice.subject_id}"}
    return Push(
        title=title,
        body=body,
        data={name: _text(value) for name, value in data.items() if value is not None},
        # A digest: APNs takes 64 bytes of collapse id at most, whatever the kind's name.
        collapse_id=hashlib.sha1(collapse[content.collapse].encode()).hexdigest() if content.collapse else None,
        ttl=int(content.ttl.total_seconds()) if content.ttl is not None else None,
        badge=unread(user).count() if content.badge and user is not None else None,
    )


@dataclass(frozen=True)
class Sent:
    """What one service answered: the attempt's `Outcome`, and whether the device's token is dead."""

    outcome: Outcome
    dead: bool = False


def _answered(response):
    return {"headers": dict(response.headers), "body": response.text[: conf.setting("MAX_RESPONSE_BYTES")]}


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def send_apns(device, push, delivery):
    """Sends `push` to an iOS device through APNs."""
    config = conf.setting("PUSH")["APNS"]
    url = f"{APNS_HOSTS[device.environment]}/3/device/{device.token}"
    headers = {
        "apns-topic": device.app_id or config["TOPIC"],
        "apns-push-type": "alert",
        "apns-priority": "10",
        # The delivery's, on every attempt - APNs gives it back, so a support question finds the delivery.
        "apns-id": str(delivery.id),
        **({"apns-collapse-id": push.collapse_id} if push.collapse_id else {}),
        **({"apns-expiration": str(int(time.time()) + push.ttl)} if push.ttl is not None else {}),
    }
    alert = {"title": push.title, **({"body": push.body} if push.body else {})}
    aps = {"alert": alert, "sound": "default", **({"badge": push.badge} if push.badge is not None else {})}
    body = {"aps": aps, **push.data}
    request = {"method": "POST", "url": url, "headers": headers, "body": body}
    response = client().post(url, json=body, headers={**headers, "authorization": f"bearer {apns_token(config)}"})
    if response.status_code == 200:
        return Sent(Outcome(succeeded=True, status_code=200, request=request, response=_answered(response)))
    reason = _json(response).get("reason", "")
    failed = {"status_code": response.status_code, "request": request, "response": _answered(response)}
    if response.status_code == 410 or reason in APNS_DEAD:
        error = _("APNs says the token is dead (%(reason)s) - the device is deactivated.") % {"reason": reason}
        return Sent(Outcome(succeeded=False, permanent=True, error=error, **failed), dead=True)
    if reason == "ExpiredProviderToken":
        forget_token("apns")
        return Sent(Outcome(succeeded=False, error=_("APNs found the provider token expired."), **failed))
    error = _("APNs answered %(status)s (%(reason)s).") % {"status": response.status_code, "reason": reason}
    return Sent(Outcome(succeeded=False, permanent=not retryable(response.status_code), error=error, **failed))


def _fcm_dead(error):
    """Whether FCM's `error` says the token is dead: unregistered, or the token the argument it finds invalid."""
    details = error.get("details", [])
    if any(detail.get("errorCode") == "UNREGISTERED" for detail in details):
        return True
    return error.get("status") == "INVALID_ARGUMENT" and any(
        violation.get("field") == "message.token"
        for detail in details
        for violation in detail.get("fieldViolations", [])
    )


def send_fcm(device, push, delivery):
    """Sends `push` to a device through FCM - Android, or an iOS app using Firebase's token."""
    config = conf.setting("PUSH")["FCM"]
    url = FCM_SEND.format(project=config.get("PROJECT_ID") or credentials(config)["project_id"])
    android = {
        "priority": "high",
        **({"collapse_key": push.collapse_id} if push.collapse_id else {}),
        **({"ttl": f"{push.ttl}s"} if push.ttl is not None else {}),
    }
    apns_headers = {
        **({"apns-collapse-id": push.collapse_id} if push.collapse_id else {}),
        **({"apns-expiration": str(int(time.time()) + push.ttl)} if push.ttl is not None else {}),
    }
    message = {
        "token": device.token,
        "notification": {"title": push.title, **({"body": push.body} if push.body else {})},
        "data": push.data,
        "android": android,
        "apns": {
            "headers": apns_headers,
            "payload": {"aps": {"sound": "default", **({"badge": push.badge} if push.badge is not None else {})}},
        },
    }
    body = {"message": message}
    request = {"method": "POST", "url": url, "body": body}
    response = client().post(url, json=body, headers={"authorization": f"Bearer {fcm_token(config)}"})
    if response.status_code == 200:
        return Sent(Outcome(succeeded=True, status_code=200, request=request, response=_answered(response)))
    error = _json(response).get("error", {})
    failed = {"status_code": response.status_code, "request": request, "response": _answered(response)}
    if _fcm_dead(error):
        said = _("FCM says the token is dead (%(status)s) - the device is deactivated.") % {
            "status": error.get("status", "")
        }
        return Sent(Outcome(succeeded=False, permanent=True, error=said, **failed), dead=True)
    if response.status_code == 401:
        forget_token("fcm")
        return Sent(Outcome(succeeded=False, error=_("FCM found the access token expired."), **failed))
    said = _("FCM answered %(status)s (%(error)s).") % {
        "status": response.status_code,
        "error": error.get("status", ""),
    }
    return Sent(Outcome(succeeded=False, permanent=not retryable(response.status_code), error=said, **failed))


SENDERS = {Platform.APNS: send_apns, Platform.FCM: send_fcm}


class PushChannel(Channel):
    """A push to each active device of each user the notice reaches."""

    name = "push"

    def targets(self, notice, kind, users):
        devices = (
            get_device_model()
            ._default_manager.filter(user__in=[user.pk for user in users], is_active=True)
            .order_by("pk")
            .values_list("pk", "user")
        )
        return [
            Target(f"device:{pk}", get_push_delivery_model(), {"device_id": str(pk), "user_id": str(user)})
            for pk, user in devices
        ]

    def _device(self, delivery):
        device = get_device_model()._default_manager.filter(pk=delivery.push.device_id).first()
        if device is None:
            return None, _("The device was forgotten.")
        if not device.is_active:
            return None, _("The device is inactive - its token was found dead.")
        return device, None

    def unretriable(self, delivery):
        gone = super().unretriable(delivery)
        if gone is not None:
            return gone
        with in_partition(delivery.partition):
            return self._device(delivery)[1]

    def send(self, delivery):
        with in_partition(delivery.partition):
            device, gone = self._device(delivery)
            if device is None:
                return Outcome(succeeded=False, permanent=True, error=gone)
            user = get_user_model()._default_manager.filter(pk=delivery.push.user_id).first()
            push = push_for(delivery, user)
        try:
            sent = SENDERS[device.platform](device, push, delivery)
        except Refused as refused:
            return Outcome(succeeded=False, permanent=not refused.retry, error=str(refused))
        except httpx.HTTPError as error:
            return Outcome(succeeded=False, error=f"{type(error).__name__}: {error}")
        if sent.dead:
            with in_partition(delivery.partition):
                get_device_model()._default_manager.filter(pk=device.pk).update(is_active=False)
        return sent.outcome
