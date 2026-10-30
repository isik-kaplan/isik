"""
Webhooks: each delivery POSTed as JSON to an endpoint wanting its kind, signed, with its id as its
Idempotency-Key - the same on every attempt, since a receiver can't tell a retry from a first try any
other way.

The signature header - X-<PROJECT_NAME>-Signature, unless `SIGNATURE["header"]` names another - holds
`t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">`, with a second `v1=` by the endpoint's previous
secret while one is being rotated out. A receiver checks each `v1=` against its secret and accepts the
request if any matches - reading the header into a dict keeps only one of them, and refuses half the
deliveries during a rotation. The header's name is a wire contract every receiver is built against,
not a label: never derive it from a display or branding name.

Where a webhook may connect is `guard.py`'s to say. A response is read up to `MAX_RESPONSE_BYTES`, and the
whole answer has `ENDPOINT_TIMEOUT` seconds - an endpoint sending a body without end, or a byte at a time,
holds a worker no longer than that.
"""

import hashlib
import hmac
import json
import time

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F
from django.utils import timezone

from isik._internal import check_extra
from isik._internal.translation import gettext as _
from isik.django.apps.notifications import conf
from isik.django.apps.notifications.audience.swapping import get_endpoint_kind_model, get_endpoint_model
from isik.django.apps.notifications.channels import Channel, Outcome, Target, guard
from isik.django.apps.notifications.partitions import in_partition
from isik.django.apps.notifications.signals import endpoint_parked
from isik.django.apps.notifications.swapping import get_webhook_delivery_model


check_extra("notifications", "httpx")

import httpx  # noqa: E402


METHOD = "POST"


def sign(secret, timestamp, body):
    """The hex HMAC-SHA256 of `"<timestamp>.<body>"` by `secret`."""
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def signature(endpoint, timestamp, body):
    """The signature header's value - a `v1=` by each of the endpoint's secrets, the current one first."""
    secrets = [endpoint.secret, *([endpoint.secret_previous] if endpoint.secret_previous else [])]
    return ",".join([f"t={timestamp}", *(f"v1={sign(secret, timestamp, body)}" for secret in secrets)])


def payload(delivery):
    """What a delivery POSTs."""
    notice = delivery.notice
    return {
        "id": str(delivery.id),
        "kind": notice.kind.name,
        "variant": notice.variant,
        "occurred_at": notice.occurred_at,
        "subject": {"label": notice.subject_label, "id": notice.subject_id, "partition": notice.subject_partition},
        "data": notice.snapshot,
        "previous": notice.previous,
    }


def retryable(status_code):
    """Whether a failure answered with `status_code` is worth trying again - one of `RETRYABLE_STATUSES`."""
    return status_code in conf.setting("RETRYABLE_STATUSES")


class TooSlow(Exception):
    """The endpoint's answer took longer than `ENDPOINT_TIMEOUT` in all."""


def read(response, deadline):
    """Up to `MAX_RESPONSE_BYTES` of `response`'s body, as text - raising `TooSlow` once `deadline` passes."""
    limit, timeout, read_so_far = conf.setting("MAX_RESPONSE_BYTES"), conf.setting("ENDPOINT_TIMEOUT"), b""
    for chunk in response.iter_bytes():
        read_so_far += chunk
        if len(read_so_far) >= limit:
            break
        if time.monotonic() > deadline:
            raise TooSlow(_("The endpoint took longer than %(seconds)s seconds to answer.") % {"seconds": timeout})
    return read_so_far[:limit].decode(errors="replace")


class WebhookChannel(Channel):
    """POSTs to every active, unparked endpoint wanting the notice's kind and variant - whoever the audience is."""

    name = "webhook"

    def targets(self, notice, kind, users):
        from isik.django.apps.notifications.kinds import matching_variant

        wanted = matching_variant(
            get_endpoint_kind_model()._default_manager.filter(
                kind_id=notice.kind_id, endpoint__is_active=True, endpoint__parked_at__isnull=True
            ),
            notice.variant,
        )
        owner = conf.setting("ENDPOINT_OWNER_FIELD")
        if owner is not None:
            # An owned endpoint hears only what its owner would: the notice's audience - before any
            # subscription, the endpoint's own kinds being its choice - and no one else's.
            audience = [user.pk for user in kind.audience.recipients(notice)]
            wanted = wanted.filter(**{f"endpoint__{owner}__in": audience})
        endpoints = sorted({str(pk) for pk in wanted.values_list("endpoint", flat=True)})
        return [Target(f"endpoint:{pk}", get_webhook_delivery_model(), {"endpoint_id": pk}) for pk in endpoints]

    def _endpoint(self, delivery):
        endpoint = get_endpoint_model()._default_manager.filter(pk=delivery.webhook.endpoint_id).first()
        if endpoint is None:
            return None, _("The endpoint no longer exists.")
        if not endpoint.is_active:
            return None, _("The endpoint is inactive.")
        if endpoint.parked_at is not None:
            return None, _("The endpoint is parked - unpark it first.")
        return endpoint, None

    def unretriable(self, delivery):
        gone = super().unretriable(delivery)
        if gone is not None:
            return gone
        with in_partition(delivery.partition):
            return self._endpoint(delivery)[1]

    def send(self, delivery):
        with in_partition(delivery.partition):
            endpoint, gone = self._endpoint(delivery)
        if endpoint is None:
            return Outcome(succeeded=False, permanent=True, error=gone)
        if httpx.URL(endpoint.url).scheme not in ("http", "https"):
            return Outcome(succeeded=False, permanent=True, error=_("The endpoint's URL isn't http or https."))
        body = json.dumps(payload(delivery), cls=DjangoJSONEncoder, separators=(",", ":")).encode()
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(delivery.id),
            conf.signature_header(): signature(endpoint, timestamp, body),
        }
        request = {"method": METHOD, "url": endpoint.url, "headers": headers, "body": json.loads(body)}
        timeout = conf.setting("ENDPOINT_TIMEOUT")
        deadline = time.monotonic() + timeout
        try:
            if conf.setting("WEBHOOK_PROXY"):
                guard.check_url(endpoint.url)
            with httpx.Client(transport=guard.transport(), timeout=timeout, trust_env=False) as client:
                with client.stream(METHOD, endpoint.url, content=body, headers=headers) as response:
                    answered = {"headers": dict(response.headers), "body": read(response, deadline)}
        except guard.RefusedTarget as refused:
            return Outcome(succeeded=False, permanent=True, request=request, error=str(refused))
        except (httpx.HTTPError, TooSlow) as error:
            return Outcome(succeeded=False, request=request, error=f"{type(error).__name__}: {error}")
        if response.is_success:
            return Outcome(succeeded=True, status_code=response.status_code, request=request, response=answered)
        return Outcome(
            succeeded=False,
            permanent=not retryable(response.status_code),
            status_code=response.status_code,
            request=request,
            response=answered,
            error=_("The endpoint answered %(status)s.") % {"status": response.status_code},
        )

    def attempted(self, delivery, outcome):
        """Counts the endpoint's failures in a row - parking it at `DISABLE_AFTER` - or clears them."""
        with in_partition(delivery.partition):
            endpoints = get_endpoint_model()._default_manager.filter(pk=delivery.webhook.endpoint_id)
            if outcome.succeeded:
                endpoints.update(consecutive_failures=0)
                return
            endpoints.update(consecutive_failures=F("consecutive_failures") + 1)
            # One update parks it, so two workers failing at once park it - and say so - once.
            parking = endpoints.filter(parked_at__isnull=True, consecutive_failures__gte=conf.setting("DISABLE_AFTER"))
            if parking.update(parked_at=timezone.now()):
                parked = endpoints.get()
                endpoint_parked.send(sender=type(parked), endpoint=parked)
