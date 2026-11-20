from django.db import models

from isik._internal.translation import gettext_lazy


class EventState(models.TextChoices):
    """Where a received event is in being handled."""

    PENDING = "pending", gettext_lazy("waiting to be handled")
    PROCESSING = "processing", gettext_lazy("being handled")
    PROCESSED = "processed", gettext_lazy("handled")
    # No handler is declared for its source and type: written down, and nothing done.
    IGNORED = "ignored", gettext_lazy("no handler")
    ABANDONED = "abandoned", gettext_lazy("given up on")


# The states an event is done in - kept until RETENTION, and never handled again but by a retry by hand.
DONE = (EventState.PROCESSED, EventState.IGNORED, EventState.ABANDONED)


class PayloadKept(models.TextChoices):
    """How long a source's events keep the payload the sender posted."""

    # Until the event itself is pruned.
    WITH_THE_EVENT = "with_the_event", gettext_lazy("for as long as the event is kept")
    # Until it's handled - then dropped, the row kept to dedupe and to say what happened.
    UNTIL_HANDLED = "until_handled", gettext_lazy("until it's handled")
