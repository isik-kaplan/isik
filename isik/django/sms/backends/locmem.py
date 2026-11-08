"""
A sender keeping each message in `isik.django.sms.outbox`, for tests - what isik's pytest plugin makes every
configured sender during a test run, emptying the outbox before each test.

A message goes the whole way a real sender's would - `pre_send`, the numbers checked, `post_send` - and a
test scripts what a number answers with `respond()`:

    respond("+14155550100", Status.REJECTED)               # as an opted-out number would
    respond("+14155550101", SMSRateLimited("Slow down"))    # raised, as a rate-limited provider would
"""

import copy
import itertools

from isik.django import sms
from isik.django.sms.backends.base import BaseSMSBackend
from isik.django.sms.message import REFUSED, RecipientStatus, Status


# {number: a Status, RecipientStatus or exception} - what respond() scripts, emptied with the outbox.
responses = {}
_ids = itertools.count(1)


def respond(number, response):
    """Answers a message to `number` with `response` - a `Status`, a `RecipientStatus`, or an exception raised."""
    responses[number] = response


def empty():
    """Empties the outbox, and forgets every scripted response."""
    sms.outbox = []
    responses.clear()


class SMSBackend(BaseSMSBackend):
    """
    Each message kept in `isik.django.sms.outbox` - a copy, with `send_status` and `sent_using` - and each
    number answered `queued` with an id of its own, unless `respond()` scripted otherwise. Every option a
    real sender takes is accepted and left unused, so a configured sender becomes this one whole.
    """

    features = frozenset({"media_urls", "metadata", "send_at", "validity", "message_type"})
    # It stands in for the configured sender during a test run, whose options it's given - and a run has DEBUG
    # off, so isik.W010 would warn of every test run.
    takes_any_option = True

    def __init__(self, *, fail_silently=False, from_=None, status_callback=None, alias="default", **options):
        super().__init__(alias=alias, fail_silently=fail_silently, from_=from_, status_callback=status_callback)
        if not hasattr(sms, "outbox"):
            sms.outbox = []

    def send_one(self, message, recipient):
        response = responses.get(recipient, Status.QUEUED)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, RecipientStatus):
            return response
        return RecipientStatus(response, message_id=None if response in REFUSED else f"locmem-{next(_ids)}")

    def _send(self, message):
        sent = super()._send(message)
        if sent:
            sms.outbox.append(copy.deepcopy(message))
        return sent
