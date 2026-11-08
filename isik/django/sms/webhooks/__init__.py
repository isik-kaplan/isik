"""
What a provider posts back - delivery reports and incoming messages - each sent as a signal (`delivery`,
`inbound`) once its signature is the provider's. `twilio` holds Twilio's views.
"""

from dataclasses import dataclass, field

from isik.django.sms.message import Status


# How far along each status is - a report for an earlier one, arriving late, never undoes a later one. Twilio
# doesn't promise its reports arrive in order.
RANKS = {
    Status.QUEUED: 0,
    Status.SCHEDULED: 0,
    Status.SENT: 1,
    Status.UNKNOWN: 1,
    Status.DELIVERED: 2,
    Status.FAILED: 2,
    Status.INVALID: 2,
    Status.REJECTED: 2,
}


def supersedes(status, previous):
    """Whether a report saying `status` moves on from `previous` - a project keeping the latest checks this."""
    return previous is None or RANKS[status] >= RANKS[previous]


@dataclass
class DeliveryEvent:
    """A provider's report on a message to one number: its id for it, how it stands, and why where it failed."""

    provider: str
    message_id: str
    status: Status
    recipient: str | None = None
    error_code: str | None = None
    metadata: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class InboundEvent:
    """
    A message someone sent the project's number. `is_opt_out` says it asks to hear nothing more - STOP and the
    like, which the provider honors itself; the project records it.
    """

    provider: str
    message_id: str | None
    from_: str
    to: str
    body: str
    media_urls: list = field(default_factory=list)
    is_opt_out: bool = False
    raw: dict = field(default_factory=dict)
