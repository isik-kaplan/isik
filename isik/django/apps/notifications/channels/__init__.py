"""
Channels - how a delivery goes out. Each one says who a notice reaches on it (`targets()`, called inside
the partition being delivered to) and sends one delivery (`send()`). Three ship - `inbox`, `email` and
`webhook` - and a project registers its own:

    register_channel("sms", "acme.notifications.SmsChannel")

`CHANNELS` decides which a kind may name.
"""

from dataclasses import dataclass, field

from django.utils.module_loading import import_string

from isik._internal.translation import gettext as _


@dataclass(frozen=True)
class Target:
    """One target a notice reaches on a channel: its key, unique per notice, and its delivery's child row."""

    key: str
    child: type
    fields: dict


@dataclass(frozen=True)
class Outcome:
    """
    What one attempt came to. A failure is tried again unless it's `permanent` - a webhook endpoint
    answering 404, say.
    """

    succeeded: bool
    permanent: bool = False
    status_code: int = None
    request: dict = field(default_factory=dict)
    response: dict = None
    error: str = ""


class Channel:
    """The base of a channel."""

    name = None

    def targets(self, notice, kind, users):
        """The `Target`s `notice` reaches on this channel - given the `users` its audience and policy chose."""
        raise NotImplementedError

    def send(self, delivery):
        """Sends `delivery` - returning an `Outcome`, or raising, which counts as a failure to try again."""
        raise NotImplementedError

    def attempted(self, delivery, outcome):
        """Called after each attempt, with what it came to."""

    def unretriable(self, delivery):
        """Why `delivery` can't be sent again by hand, or None - by default, only that its partition is gone."""
        from isik.django.apps.notifications.partitions import PartitionGone, in_partition

        try:
            with in_partition(delivery.partition):
                return None
        except PartitionGone as gone:
            return str(gone)


registered = {
    "inbox": "isik.django.apps.notifications.channels.inbox.InboxChannel",
    "email": "isik.django.apps.notifications.channels.email.EmailChannel",
    "webhook": "isik.django.apps.notifications.channels.webhook.WebhookChannel",
}


def register_channel(name, channel):
    """Registers `channel` - a `Channel` subclass or its dotted path - as `name`."""
    registered[name] = channel


def get_channel(name):
    """The channel registered as `name`. Raises `LookupError` for one that isn't."""
    try:
        channel = registered[name]
    except KeyError:
        raise LookupError(_("No channel is registered as %(name)r.") % {"name": name}) from None
    if isinstance(channel, str):
        channel = import_string(channel)
    return channel()
