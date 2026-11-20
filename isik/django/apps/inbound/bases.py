"""
The inbound event's abstract base - `AbstractInboundEvent` - for a project to own the events' table: subclass it,
and name the subclass in `ISIK['DJANGO']['INBOUND']['EVENT_MODEL']`.

    class InboundEvent(AbstractInboundEvent):
        class Meta:
            db_table = "inbound_event"

    ISIK = {"DJANGO": {"INBOUND": {"EVENT_MODEL": "billing.InboundEvent"}}}

Each concrete subclass gets the events' rules when it's prepared, whatever its own Meta inherits: one event per
source and sender's id, and the index work() finds due events by. Their names are stable - a project may name them.
"""

from django.db import models
from django.db.models.functions import Now
from django.db.models.signals import class_prepared

from isik._internal.translation import gettext_lazy
from isik.django.apps.common.db.constraints import fitted_name
from isik.django.apps.common.db.models import new_primary_key
from isik.django.apps.inbound.choices import EventState


class AbstractInboundEvent(models.Model):
    """
    One webhook a sender posted: written down once, when it's received - verified, in its partition - and handled
    by a task. A sender's resend of the same event - its source and its id again - is acknowledged and dropped.
    """

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        default=new_primary_key,
        help_text=gettext_lazy("The event's own identifier."),
        db_comment="The event's own identifier - isik's, not the sender's.",
        verbose_name=gettext_lazy("ID"),
    )
    source = models.CharField(
        max_length=100,
        help_text=gettext_lazy("The declared source it came from."),
        db_comment="The declared source it came from - the sender, as the project names it.",
        verbose_name=gettext_lazy("source"),
    )
    event_id = models.TextField(
        null=True,
        blank=True,
        help_text=gettext_lazy("The sender's id for it - what a resend repeats."),
        db_comment="The sender's id for the event - one row per source and id. Null for a source declared as "
        "naming none, whose resends aren't told apart.",
        verbose_name=gettext_lazy("sender's event id"),
    )
    event_type = models.CharField(
        max_length=200,
        blank=True,
        help_text=gettext_lazy("What kind of event the sender says it is."),
        db_comment="The sender's kind of event - which handler takes it.",
        verbose_name=gettext_lazy("event type"),
    )
    partition = models.TextField(
        null=True,
        blank=True,
        help_text=gettext_lazy("The partition it was received in."),
        db_comment="The partition the source named for the request, where the event is handled. Null with none.",
        verbose_name=gettext_lazy("partition"),
    )
    payload = models.JSONField(
        null=True,
        blank=True,
        help_text=gettext_lazy("What the sender posted, as its source reads it."),
        db_comment="The body the sender posted, parsed - null once handled for a source that keeps it until then.",
        verbose_name=gettext_lazy("payload"),
    )
    received_at = models.DateTimeField(
        default=Now(),
        db_default=Now(),
        help_text=gettext_lazy("When it was received."),
        db_comment="When the request bringing it was received.",
        verbose_name=gettext_lazy("received at"),
    )
    state = models.CharField(
        max_length=16,
        choices=EventState.choices,
        default=EventState.PENDING,
        db_default=EventState.PENDING,
        help_text=gettext_lazy("Where it is in being handled."),
        db_comment="pending, processing, processed, ignored (no handler) or abandoned.",
        verbose_name=gettext_lazy("state"),
    )
    attempt_count = models.PositiveIntegerField(
        default=0,
        db_default=0,
        help_text=gettext_lazy("How many times it's been handed to its handler."),
        db_comment="Attempts to handle it since it was received, or last retried by hand.",
        verbose_name=gettext_lazy("attempts"),
    )
    next_attempt_at = models.DateTimeField(
        default=Now(),
        db_default=Now(),
        help_text=gettext_lazy("When it's next tried."),
        db_comment="When a pending event is next due to be handled.",
        verbose_name=gettext_lazy("next attempt at"),
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When a worker took it."),
        db_comment="When a worker claimed it, while processing - the mark a stale worker's finish is refused by.",
        verbose_name=gettext_lazy("claimed at"),
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When it was done with."),
        db_comment="When it was handled, ignored or given up on.",
        verbose_name=gettext_lazy("processed at"),
    )
    last_error = models.TextField(
        blank=True,
        default="",
        db_default="",
        help_text=gettext_lazy("Why its last attempt failed."),
        db_comment="What the handler raised on its last failed attempt, else empty.",
        verbose_name=gettext_lazy("last error"),
    )

    class Meta:
        abstract = True
        verbose_name = gettext_lazy("inbound event")
        verbose_name_plural = gettext_lazy("inbound events")

    def __str__(self):
        return f"{self.source}:{self.event_type}:{self.event_id or self.pk}"


def _rules(model):
    table = model._meta.db_table
    return (
        [
            models.UniqueConstraint(
                fields=["source", "event_id"],
                condition=models.Q(event_id__isnull=False),
                name=fitted_name(table, "once"),
                violation_error_message=gettext_lazy("This event was received already."),
                violation_error_code="event_received",
            )
        ],
        [
            models.Index(
                fields=["next_attempt_at"],
                condition=models.Q(state=EventState.PENDING),
                name=fitted_name(table, "due", limit=models.Index.max_name_length),
            ),
            models.Index(
                fields=["claimed_at"],
                condition=models.Q(state=EventState.PROCESSING),
                name=fitted_name(table, "claimed", limit=models.Index.max_name_length),
            ),
            models.Index(
                fields=["received_at"], name=fitted_name(table, "received", limit=models.Index.max_name_length)
            ),
        ],
    )


def _hold_to_the_rules(sender, **kwargs):
    # On every concrete subclass: Meta constraints and indexes on an abstract base would reach only a subclass whose
    # Meta inherits the base's.
    if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, AbstractInboundEvent):
        return
    constraints, indexes = _rules(sender)
    # New lists, recorded where a migration's state is read from - see the notifications ledger's.
    sender._meta.constraints = [*sender._meta.constraints, *constraints]
    sender._meta.indexes = [*sender._meta.indexes, *indexes]
    sender._meta.original_attrs["constraints"] = sender._meta.constraints
    sender._meta.original_attrs["indexes"] = sender._meta.indexes


class_prepared.connect(_hold_to_the_rules, dispatch_uid="isik_inbound_event_rules")
