"""
The audience's abstract bases - the rows naming people: `AbstractSubscription`, `AbstractInboxItem`,
`AbstractEndpoint`, `AbstractEndpointKind` and `AbstractDevice`.

None declares `user` or `kind`: the concrete model does, so a project can key them however its database
needs (a plain foreign key, or one reaching across schemas). Each concrete subclass gets its constraints
and its `VariantGuard` when it's prepared, whatever its own Meta inherits:

    class Subscription(AbstractSubscription):
        user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
        kind = models.ForeignKey("isik_notifications.NoticeKindPolicy", on_delete=models.CASCADE)

and the project points `ISIK_NOTIFICATIONS_SUBSCRIPTION_MODEL` at it.
"""

import pgtrigger
from django.core.validators import URLValidator
from django.db import models
from django.db.models.signals import class_prepared

from isik._internal.translation import gettext_lazy
from isik.django.apps.common.db.constraints import fitted_name
from isik.django.apps.common.db.models import DatabaseTimestampsModel
from isik.django.apps.notifications.choices import Platform, PushEnvironment
from isik.django.apps.notifications.triggers import VariantGuard


WEBHOOK_SCHEMES = ("http", "https")


def _variant():
    return models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text=gettext_lazy("The kind's one variant this is for, or none for every one."),
        db_comment="The kind's declared variant this is for, or null for every variant.",
    )


class AbstractSubscription(models.Model):
    """
    A user's deviation from a kind's policy: turning a default-on kind off, or a default-off kind on - for
    every variant, or one. A user with none gets the kind's default.
    """

    variant = _variant()
    enabled = models.BooleanField(
        default=True,
        help_text=gettext_lazy("Whether the user gets the kind."),
        db_comment="Whether the user gets the kind (and variant).",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.kind_id}:{self.variant or '*'}"


class AbstractForceableSubscription(AbstractSubscription):
    """
    A subscription staff can force on or off for one person - which that person can't then change. The
    person's own choice is kept beside the forced one, so lifting the force brings it back: forced off, then
    released, and they have it on again if they'd chosen that. Opt in by subclassing this for the project's
    subscription model; the default, `AbstractSubscription`, carries none of it. Written by
    `force_subscription()` and `release_subscription()`, and history-tracked (a check holds it to that).
    """

    chosen = models.BooleanField(
        default=True,
        help_text=gettext_lazy("Whether the person chose this themselves, or only staff forced it."),
        db_comment="Whether enabled is the person's own choice (false: the row exists only for its force).",
    )
    forced = models.BooleanField(
        default=False,
        help_text=gettext_lazy("Whether staff forced it, so the person can't change it."),
        db_comment="Whether the subscription is forced - forced_enabled then decides, whatever enabled says.",
    )
    forced_enabled = models.BooleanField(
        null=True,
        blank=True,
        help_text=gettext_lazy("What it's forced to, while it's forced."),
        db_comment="The forced value while forced, else null.",
    )
    forced_source = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=gettext_lazy("The force source that forced it, while it's forced."),
        db_comment="The declared force source that forced it while forced, else empty.",
    )
    forced_reason = models.TextField(
        blank=True,
        default="",
        help_text=gettext_lazy("Why it was forced, while it's forced."),
        db_comment="Why it was forced, while forced, else empty.",
    )

    class Meta:
        abstract = True


class AbstractInboxItem(models.Model):
    """One notification in a user's in-app inbox - a copy of what its delivery carried, read or not."""

    variant = _variant()
    subject_label = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text=gettext_lazy("The model label of the row it's about, or none."),
        db_comment="The app_label.Model of the row it's about, or null.",
    )
    subject_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=gettext_lazy("The primary key of the row it's about, or none."),
        db_comment="The primary key of the row it's about, as text, or null.",
    )
    actor_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=gettext_lazy("Who acted, as the notice recorded them, or none."),
        db_comment="The primary key of who acted, copied from the notice, or null. Not shown to the recipient.",
    )
    context = models.JSONField(
        default=dict,
        blank=True,
        help_text=gettext_lazy("The history context the notice kept, beside who acted."),
        db_comment="The notice's context, copied from it. Not shown to the recipient.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=gettext_lazy("What the notice carried."),
        db_comment="What the notice carried: its snapshot, and previous when it kept one.",
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When the user read it."),
        db_comment="When the user read it, or null.",
    )
    delivery_id = models.UUIDField(
        unique=True,
        help_text=gettext_lazy("The delivery that put it here."),
        db_comment="The ledger delivery that put it here - unique, so a delivery lands once however often it's tried.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text=gettext_lazy("When it landed."),
        db_comment="When it landed.",
    )

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind_id}:{self.subject_label}:{self.subject_id}"


class AbstractEndpoint(DatabaseTimestampsModel):
    """
    A URL that kinds are POSTed to, signed with its secret. Override `secret` (and `secret_previous`) with
    an encrypted field of your own - a field declared on a subclass replaces an abstract base's. Its
    `updated_at` is its version, so a change made through the API names the version it was made against.
    """

    url = models.URLField(
        max_length=2000,
        # http and https alone - URLField also takes ftp and ftps, which a webhook isn't sent to.
        validators=[URLValidator(schemes=WEBHOOK_SCHEMES)],
        help_text=gettext_lazy("Where deliveries are POSTed."),
        db_comment="Where deliveries are POSTed.",
    )
    secret = models.CharField(
        max_length=255,
        help_text=gettext_lazy("The secret every delivery is signed with."),
        db_comment="The secret every delivery is signed with.",
    )
    secret_previous = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=gettext_lazy("The secret being rotated out - deliveries are signed with it too until it's cleared."),
        db_comment="The secret being rotated out, or null. While it's set, deliveries carry a signature by each.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=gettext_lazy("Whether deliveries are sent to it."),
        db_comment="Whether deliveries are sent to it.",
    )
    consecutive_failures = models.PositiveIntegerField(
        default=0,
        help_text=gettext_lazy("How many deliveries in a row have failed."),
        db_comment="How many attempts in a row have failed. DISABLE_AFTER of them park it.",
    )
    parked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When it was parked for failing - nothing is sent to it until it's unparked."),
        db_comment="When it was parked for failing, or null. Nothing is sent to a parked endpoint.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return self.url

    def unpark(self):
        """Sends to it again, its failures forgotten."""
        self.parked_at, self.consecutive_failures = None, 0
        self.save()


class AbstractEndpointKind(models.Model):
    """A kind (and variant, or every variant) an endpoint wants POSTed."""

    variant = _variant()

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.kind_id}:{self.variant or '*'}"


class AbstractDevice(DatabaseTimestampsModel):
    """
    One of a user's phones (or other app installs), as its push service knows it. A token belongs to one
    install, so registering one already known moves it to whoever registers it - the person signed in on
    that phone now. The concrete model declares `user`.
    """

    platform = models.CharField(
        max_length=16,
        choices=Platform.choices,
        help_text=gettext_lazy("The service its pushes go through - apns or fcm."),
        db_comment="The push service its token is for: apns or fcm.",
    )
    token = models.CharField(
        max_length=4096,
        help_text=gettext_lazy("The token its push service gave the app on it."),
        db_comment="The push service's token for the install - unique with platform.",
    )
    environment = models.CharField(
        max_length=16,
        choices=PushEnvironment.choices,
        default=PushEnvironment.PRODUCTION,
        help_text=gettext_lazy("Which APNs issued its token - sandbox for a development build."),
        db_comment="production or sandbox: which APNs host its pushes go to. FCM ignores it.",
    )
    app_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=gettext_lazy("The app's bundle identifier, where it isn't the project's one."),
        db_comment="The APNs topic - the app's bundle id - or empty for PUSH['APNS']['TOPIC'].",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=gettext_lazy("Whether pushes are sent to it."),
        db_comment="Whether pushes are sent to it - false once its push service said the token is dead.",
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("When the app on it last registered."),
        db_comment="When the app last registered its token, or null.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.platform}:{self.token[:12]}"


# {base: (the field owning a row, beside "kind" and "variant", or None)} - the owner makes the
# no-variant row exclusive with the rest of its owner's rows for a kind.
GUARDED = {
    AbstractSubscription: "user",
    AbstractEndpointKind: "endpoint",
    AbstractInboxItem: None,
}


def _guard(sender, **kwargs):
    # On every concrete subclass, as isik's timestamp triggers are: Meta.constraints and Meta.triggers
    # on the abstract base would reach only a subclass whose Meta inherits the base's.
    if sender._meta.abstract or sender._meta.proxy:
        return
    if issubclass(sender, AbstractDevice):
        # One row a token: registering it again finds this one, and moves it. Left to Django to name: the
        # name reaches nothing but an error, and a schema copied whole renames a unique over two columns anyway.
        sender._meta.unique_together = (*sender._meta.unique_together, ("platform", "token"))
        sender._meta.original_attrs["unique_together"] = sender._meta.unique_together
    for base, owner in GUARDED.items():
        if not issubclass(sender, base):
            continue
        pgtrigger.register(VariantGuard(name="guard_variant", owner=owner))(sender)
        if owner:
            # A new list: one inherited from a parent Meta is that Meta's own.
            sender._meta.constraints = [
                *sender._meta.constraints,
                models.UniqueConstraint(
                    fields=[owner, "kind", "variant"],
                    nulls_distinct=False,
                    name=fitted_name(sender._meta.db_table, "once"),
                ),
                # A forceable subscription's force is whole, or absent.
                *(_force_constraints(sender) if issubclass(sender, AbstractForceableSubscription) else ()),
            ]
            # What a migration's state is built from - a constraint only in _meta.constraints never reaches it.
            sender._meta.original_attrs["constraints"] = sender._meta.constraints


def _force_constraints(model):
    table = model._meta.db_table
    unforced = models.Q(forced=False, forced_enabled__isnull=True, forced_source="", forced_reason="")
    forced = models.Q(forced=True, forced_enabled__isnull=False) & ~models.Q(forced_source="")
    return [
        # Forced, with what it's forced to and by whom - or none of it.
        models.CheckConstraint(condition=unforced | forced, name=fitted_name(table, "forced_exactly")),
        # A row is the person's choice, or a force's - never neither.
        models.CheckConstraint(
            condition=models.Q(chosen=True) | models.Q(forced=True), name=fitted_name(table, "chosen_or_forced")
        ),
    ]


class_prepared.connect(_guard, dispatch_uid="isik_notifications_audience_guard")
