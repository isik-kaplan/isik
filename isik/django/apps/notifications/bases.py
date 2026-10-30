"""
The ledger's abstract bases - `AbstractNoticeKindPolicy`, `AbstractNotice`, `AbstractFanOutShard`,
`AbstractDelivery`, its four channel children and `AbstractDeliveryAttempt` - for a project to own the
ledger's tables: subclass one, and point its `ISIK_NOTIFICATIONS_*_MODEL` setting (see `swapping.py`) at
the subclass.

    class Notice(AbstractNotice):
        class Meta:
            db_table = "notifications_notice"

    ISIK_NOTIFICATIONS_NOTICE_MODEL = "notifications.Notice"

Each concrete subclass gets the ledger's rules when it's prepared, whatever its own Meta inherits: a notice
frozen once written and stamped once, attempts append-only, no TRUNCATE of either, a kind never deleted or
renamed, and the unique constraints and indexes the pipeline relies on. Their names are stable - a project
may name them. The keys between ledger models point at the configured models, so a project owning several
has them key to each other.

Not one key to a user between them - the rows naming people are the audience app's. Nor is any of it
tracked by pghistory: the ledger is a record already, and its protections are its own triggers.
"""

import pgtrigger
from django.db import models
from django.db.models.functions import Now
from django.db.models.signals import class_prepared
from django.utils import timezone

from isik._internal.translation import gettext_lazy
from isik.django.apps.common.db.conditions import AnyChange
from isik.django.apps.common.db.constraints import fitted_name
from isik.django.apps.common.db.models import new_primary_key
from isik.django.apps.common.db.triggers import ProtectTruncate
from isik.django.apps.notifications.choices import DeliveryState, Policy, Scope, ShardState, Source
from isik.django.apps.notifications.swapping import (
    DELIVERY_MODEL,
    KIND_POLICY_MODEL,
    NOTICE_MODEL,
    model_name,
)


class AbstractNoticeKindPolicy(models.Model):
    """
    One declared kind, mirrored from its declaration after every migrate - the row its notices and the
    audience's subscriptions and endpoint kinds key to. The declaration is the source of truth; a row is
    never deleted, only retired, since notices outlive their kind.
    """

    kind = models.CharField(
        primary_key=True,
        max_length=100,
        help_text=gettext_lazy("The kind's name, as its declaration gives it."),
        db_comment="The kind's name, as its declaration gives it. Never changes.",
    )
    policy = models.CharField(
        max_length=16,
        choices=Policy.choices,
        help_text=gettext_lazy(
            "Who gets the kind: everyone its audience names, opted-out aside, or only the opted-in."
        ),
        db_comment="mandatory, default_on or default_off - mirrored from the declaration.",
    )
    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        help_text=gettext_lazy("Whether the kind's audience lives in one partition or many."),
        db_comment="single_partition or cross_partition - mirrored from the declaration.",
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        help_text=gettext_lazy("What writes the kind's notices: a trigger, the application, or record_email()."),
        db_comment="trigger, application or recorded - mirrored from the declaration.",
    )
    variants = models.JSONField(
        default=list,
        blank=True,
        help_text=gettext_lazy("The variants the kind declares - the only ones a notice or subscription may name."),
        db_comment="The variants the kind declares. A subscription, endpoint kind or inbox item naming another "
        "is refused.",
    )
    retired_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When the kind was retired. A retired kind writes no more notices."),
        db_comment="When the kind was retired, or null. A retired kind writes no more notices.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return self.kind


class AbstractNotice(models.Model):
    """
    That something happened: one row per kind (and variant) a change is worth telling of, written in the
    transaction that made the change - by the kind's trigger, or by `raise_notice()`. Frozen once written,
    `fanned_out_at` aside, which is stamped once: a null `fanned_out_at` is the queue `fan_out` takes from.
    """

    # A sequence, not new_primary_key(): the notice trigger writes notices in SQL, which no Python key
    # function reaches. A project keying them otherwise gives its own id a db_default.
    id = models.BigAutoField(
        primary_key=True,
        help_text=gettext_lazy("The notice's own identifier."),
        db_comment="The notice's own identifier.",
    )
    kind = models.ForeignKey(
        model_name(KIND_POLICY_MODEL),
        on_delete=models.PROTECT,
        related_name="notices",
        help_text=gettext_lazy("What kind of thing happened."),
        db_comment="What kind of thing happened.",
    )
    variant = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=gettext_lazy("Which of the kind's variants happened, or none."),
        db_comment="Which of the kind's declared variants happened, or null.",
    )
    subject_label = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text=gettext_lazy("The model label of the row it happened to, or none."),
        db_comment="The app_label.Model of the row it happened to. Null for a notice no row is behind.",
    )
    subject_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=gettext_lazy("The primary key of the row it happened to, or none."),
        db_comment="The row's primary key, as text. Not a key: the row may be deleted, and the record stays.",
    )
    subject_partition = models.TextField(
        null=True,
        blank=True,
        help_text=gettext_lazy("The partition the row it happened to belongs to, or none."),
        db_comment="The partition the subject belongs to. Not a key: a partition may be dropped, and the record stays.",
    )
    snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text=gettext_lazy("The subject's columns the kind allows, as they were after the change."),
        db_comment="The subject's allow-listed columns, as they were after the change. Never another column.",
    )
    previous = models.JSONField(
        null=True,
        blank=True,
        help_text=gettext_lazy("The same columns as they were before the change, for an update."),
        db_comment="The allow-listed columns as they were before the change. Null unless the kind keeps them.",
    )
    actor_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text=gettext_lazy("Who acted, or none - a migration, a command, a scheduled task."),
        db_comment="ACTOR_CONTEXT_KEY of the history context the notice was written in, as text, or null.",
    )
    context = models.JSONField(
        default=dict,
        blank=True,
        help_text=gettext_lazy("What the history context said that the project keeps on its notices."),
        db_comment="NOTICE_CONTEXT_KEYS of the history context the notice was written in - those it held.",
    )
    pgh_context = models.ForeignKey(
        "pghistory.Context",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=gettext_lazy("The history context of the change - who made it, and from where."),
        db_comment="The pghistory context the change was made in - shared with the event rows it wrote.",
    )
    occurred_at = models.DateTimeField(
        db_default=Now(),
        default=timezone.now,
        help_text=gettext_lazy("When it happened."),
        db_comment="When it happened - when its transaction began.",
    )
    fanned_out_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When its deliveries were written. Unset, it's waiting to be."),
        db_comment="When its deliveries (or shards) were written. Null is the fan-out queue. Set once.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.kind_id}#{self.pk}"


class AbstractFanOutShard(models.Model):
    """One partition a cross-partition notice reaches - claimed by a worker, which writes its deliveries there."""

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        default=new_primary_key,
        help_text=gettext_lazy("The shard's own identifier."),
        db_comment="The shard's own identifier.",
    )
    notice = models.ForeignKey(
        model_name(NOTICE_MODEL),
        on_delete=models.CASCADE,
        related_name="shards",
        help_text=gettext_lazy("The notice reaching the partition."),
        db_comment="The notice reaching the partition.",
    )
    partition = models.TextField(
        null=True,
        blank=True,
        help_text=gettext_lazy("The partition's key, or none for a project with no partitions."),
        db_comment="The partition's key, as PARTITIONS gives it - null with none. Not a key: a partition may "
        "be dropped.",
    )
    state = models.CharField(
        max_length=16,
        choices=ShardState.choices,
        default=ShardState.PENDING,
        help_text=gettext_lazy("pending, claimed, finished or abandoned."),
        db_comment="pending, claimed (a worker has it), finished or abandoned.",
    )
    attempt_count = models.PositiveIntegerField(
        default=0,
        help_text=gettext_lazy("How many times a worker has claimed it."),
        db_comment="How many times a worker has claimed it.",
    )
    next_attempt_at = models.DateTimeField(
        default=timezone.now,
        help_text=gettext_lazy("When it's next due."),
        db_comment="When it's next due to be claimed.",
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When a worker last claimed it."),
        db_comment="When a worker last claimed it - reap_stranded takes back one claimed too long ago.",
    )
    claimed_by = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=gettext_lazy("The task that last claimed it."),
        db_comment="The id of the task that last claimed it.",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When its deliveries were written."),
        db_comment="When its deliveries were written, or null.",
    )
    last_error = models.TextField(
        blank=True,
        default="",
        help_text=gettext_lazy("Why its last claim failed."),
        db_comment="Why its last claim failed, or empty.",
    )

    class Meta:
        abstract = True


class AbstractDelivery(models.Model):
    """
    One message a notice sends: to one target, on one channel. Its target lives in the channel's own
    child row, never here. Its id is the Idempotency-Key a webhook receiver dedupes on - the same on
    every attempt.
    """

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        default=new_primary_key,
        help_text=gettext_lazy("The delivery's own identifier - the key a receiver dedupes it by."),
        db_comment="The delivery's own identifier, sent as its Idempotency-Key on every attempt.",
    )
    notice = models.ForeignKey(
        model_name(NOTICE_MODEL),
        on_delete=models.CASCADE,
        related_name="deliveries",
        help_text=gettext_lazy("The notice it sends."),
        db_comment="The notice it sends.",
    )
    channel = models.CharField(
        max_length=32,
        help_text=gettext_lazy("The channel it goes out on."),
        db_comment="The registered channel it goes out on - inbox, email, webhook, or a project's own.",
    )
    partition = models.TextField(
        null=True,
        blank=True,
        help_text=gettext_lazy("The partition its target lives in, or none."),
        db_comment="The partition its target lives in. Null when the project declares no partitions.",
    )
    target_key = models.CharField(
        max_length=255,
        help_text=gettext_lazy("Its target, as one key - what keeps a notice from reaching one target twice."),
        db_comment="Its target as one opaque key, unique per notice and channel.",
    )
    state = models.CharField(
        max_length=16,
        choices=DeliveryState.choices,
        default=DeliveryState.PENDING,
        help_text=gettext_lazy("pending, sending, sent or abandoned."),
        db_comment="pending (due at next_attempt_at), sending, sent or abandoned.",
    )
    attempt_count = models.PositiveIntegerField(
        default=0,
        help_text=gettext_lazy("How many times it's been tried."),
        db_comment="How many times it's been tried, in every run.",
    )
    attempts_before_run = models.PositiveIntegerField(
        default=0,
        help_text=gettext_lazy("How many times it had been tried when its current run began."),
        db_comment="attempt_count when the current run began - a manual retry starts one. MAX_ATTEMPTS counts from it.",
    )
    retry_trigger = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=gettext_lazy("What asked for its next attempt, when it wasn't the send task."),
        db_comment="What asked for its next attempt - manual - or empty. The attempt records it, and it's cleared.",
    )
    next_attempt_at = models.DateTimeField(
        default=timezone.now,
        help_text=gettext_lazy("When it's next due."),
        db_comment="When it's next due to be tried.",
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When a worker last took it to send."),
        db_comment="When a worker last took it to send - reap_stranded takes back one sending too long.",
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When it was sent."),
        db_comment="When it was sent, or null.",
    )
    created_at = models.DateTimeField(
        db_default=Now(),
        default=timezone.now,
        help_text=gettext_lazy("When it was written."),
        db_comment="When it was written.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.channel}:{self.target_key}"


def _delivery_key(related_name):
    return models.OneToOneField(
        model_name(DELIVERY_MODEL),
        primary_key=True,
        on_delete=models.CASCADE,
        related_name=related_name,
        help_text=gettext_lazy("The delivery this is the target of."),
        db_comment="The delivery this is the target of.",
    )


class AbstractWebhookDelivery(models.Model):
    """A delivery's endpoint - its key in the partition the delivery names."""

    delivery = _delivery_key("webhook")
    endpoint_id = models.CharField(
        max_length=255,
        help_text=gettext_lazy("The endpoint's primary key."),
        db_comment="The endpoint's primary key, as text, in the delivery's partition. Not a key: it may be deleted.",
    )

    class Meta:
        abstract = True


class AbstractEmailDelivery(models.Model):
    """A delivery's address, and the user it belongs to when it's a user's."""

    delivery = _delivery_key("email")
    address = models.CharField(
        max_length=254,
        help_text=gettext_lazy("The address it's sent to."),
        db_comment="The address it's sent to.",
    )
    user_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=gettext_lazy("The user the address belongs to, or none."),
        db_comment="The primary key of the user the address belongs to, as text, or null.",
    )
    record_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        help_text=gettext_lazy("What its sender named a recorded email by, across its tries - or none."),
        db_comment="record_email()'s key: one email across its sender's tries. Null for one isik sends.",
    )

    class Meta:
        abstract = True


class AbstractInboxDelivery(models.Model):
    """A delivery's user - whose inbox it lands in."""

    delivery = _delivery_key("inbox")
    user_id = models.CharField(
        max_length=255,
        help_text=gettext_lazy("The user whose inbox it lands in."),
        db_comment="The primary key of the user whose inbox it lands in, as text, in the delivery's partition.",
    )

    class Meta:
        abstract = True


class AbstractPushDelivery(models.Model):
    """A delivery's device - one of a user's phones - and the user it's theirs."""

    delivery = _delivery_key("push")
    device_id = models.CharField(
        max_length=255,
        help_text=gettext_lazy("The device's primary key."),
        db_comment="The device's primary key, as text, in the delivery's partition. Not a key: it may be forgotten.",
    )
    user_id = models.CharField(
        max_length=255,
        help_text=gettext_lazy("The user the device is theirs."),
        db_comment="The primary key of the user whose device it is, as text, in the delivery's partition.",
    )

    class Meta:
        abstract = True


class AbstractDeliveryAttempt(models.Model):
    """One try at a delivery, and what came back. Append-only; pruned by age, being a log and not history."""

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        default=new_primary_key,
        help_text=gettext_lazy("The attempt's own identifier."),
        db_comment="The attempt's own identifier.",
    )
    delivery = models.ForeignKey(
        model_name(DELIVERY_MODEL),
        on_delete=models.CASCADE,
        related_name="attempts",
        help_text=gettext_lazy("The delivery tried."),
        db_comment="The delivery tried.",
    )
    attempt_no = models.PositiveIntegerField(
        help_text=gettext_lazy("Which try it was, from 1."),
        db_comment="Which try it was, from 1.",
    )
    request = models.JSONField(
        default=dict,
        blank=True,
        help_text=gettext_lazy("What was sent, redacted."),
        db_comment="What was sent, after REDACTOR.",
    )
    response = models.JSONField(
        null=True,
        blank=True,
        help_text=gettext_lazy("What came back, redacted and cut short."),
        db_comment="What came back, after REDACTOR, its body cut to MAX_RESPONSE_BYTES. Null when nothing did.",
    )
    status_code = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=gettext_lazy("The HTTP status that came back, or none."),
        db_comment="The HTTP status that came back. Null for a timeout, a refused connection or a non-HTTP channel.",
    )
    error = models.TextField(
        blank=True,
        default="",
        help_text=gettext_lazy("Why it failed, or nothing."),
        db_comment="Why it failed, or empty.",
    )
    succeeded = models.BooleanField(
        help_text=gettext_lazy("Whether it went through."),
        db_comment="Whether it went through.",
    )
    started_at = models.DateTimeField(
        help_text=gettext_lazy("When it began."),
        db_comment="When it began.",
    )
    duration_ms = models.PositiveIntegerField(
        help_text=gettext_lazy("How long it took, in milliseconds."),
        db_comment="How long it took, in milliseconds.",
    )
    triggered_by = models.CharField(
        max_length=32,
        help_text=gettext_lazy("What started it - the send task, the reaper, or someone by hand."),
        db_comment="What started it: send, reap_stranded, or manual.",
    )

    class Meta:
        abstract = True


def _rules(model):
    """(triggers, constraints, indexes) the ledger holds `model`'s table to - by the base it subclasses."""
    table = model._meta.db_table
    if issubclass(model, AbstractNoticeKindPolicy):
        return (
            [
                pgtrigger.ReadOnly(name="protect_kind", fields=["kind"]),
                pgtrigger.Protect(name="protect_policy_delete", operation=pgtrigger.Delete),
                # A notice outlives its kind: emptying the table would orphan every one, as deleting a row does.
                ProtectTruncate(name="protect_policy_truncate"),
            ],
            [],
            [],
        )
    if issubclass(model, AbstractNotice):
        return (
            [
                pgtrigger.Protect(
                    name="protect_notice_update",
                    operation=pgtrigger.Update,
                    condition=AnyChange(exclude=["fanned_out_at"]),
                ),
                # Stamped once: never unstamped, never restamped, and an update that doesn't stamp it is refused.
                pgtrigger.Protect(
                    name="fanned_out_once",
                    operation=pgtrigger.Update,
                    condition=pgtrigger.Q(old__fanned_out_at__isnull=False)
                    | pgtrigger.Q(new__fanned_out_at__isnull=True),
                ),
                pgtrigger.Protect(name="protect_notice_delete", operation=pgtrigger.Delete),
                ProtectTruncate(name="protect_notice_truncate"),
            ],
            [],
            [
                models.Index(
                    fields=["occurred_at"],
                    condition=models.Q(fanned_out_at__isnull=True),
                    name=fitted_name(table, "unfanned", limit=models.Index.max_name_length),
                )
            ],
        )
    if issubclass(model, AbstractFanOutShard):
        return (
            [],
            [models.UniqueConstraint(fields=["notice", "partition"], name=fitted_name(table, "once"))],
            [
                models.Index(
                    fields=["next_attempt_at"],
                    condition=models.Q(finished_at__isnull=True),
                    name=fitted_name(table, "unfinished", limit=models.Index.max_name_length),
                )
            ],
        )
    if issubclass(model, AbstractDelivery):
        return (
            [],
            [models.UniqueConstraint(fields=["notice", "channel", "target_key"], name=fitted_name(table, "once"))],
            [
                models.Index(
                    fields=["next_attempt_at"],
                    condition=models.Q(state=DeliveryState.PENDING),
                    name=fitted_name(table, "due", limit=models.Index.max_name_length),
                )
            ],
        )
    if issubclass(model, AbstractDeliveryAttempt):
        return (
            [
                pgtrigger.Protect(name="append_only", operation=pgtrigger.Update | pgtrigger.Delete),
                ProtectTruncate(name="protect_attempt_truncate"),
            ],
            [models.UniqueConstraint(fields=["delivery", "attempt_no"], name=fitted_name(table, "once"))],
            [],
        )
    return None


def _hold_to_the_rules(sender, **kwargs):
    # On every concrete subclass, as isik's timestamp triggers are: Meta.triggers, constraints and indexes on
    # an abstract base would reach only a subclass whose Meta inherits the base's.
    if sender._meta.abstract or sender._meta.proxy:
        return
    rules = _rules(sender)
    if rules is None:
        return
    triggers, constraints, indexes = rules
    pgtrigger.register(*triggers)(sender)
    # New lists, recorded where a migration's state is read from: one inherited from a parent Meta is that
    # Meta's own, and a constraint only in _meta.constraints never reaches a migration.
    sender._meta.constraints = [*sender._meta.constraints, *constraints]
    sender._meta.indexes = [*sender._meta.indexes, *indexes]
    sender._meta.original_attrs["constraints"] = sender._meta.constraints
    sender._meta.original_attrs["indexes"] = sender._meta.indexes


class_prepared.connect(_hold_to_the_rules, dispatch_uid="isik_notifications_ledger_rules")
