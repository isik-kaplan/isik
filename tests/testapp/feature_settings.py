"""The test project's features and the settings they unlock - isik-template's cases, the holder its user."""

from datetime import timedelta

from django.core.validators import MaxValueValidator
from django.db import models

from isik.django.apps.features.declarations import AtMost, Fixed, OnHolder, declare_feature, declare_setting
from isik.django.apps.features.guards import requires_feature
from tests.testapp.policies import RecentlyProved


WEBHOOKS = declare_feature("webhooks", label="Webhooks", description="Send events to your own URLs.")
LONG_LIVED_TOKENS = declare_feature("long_lived_tokens", label="Long-lived tokens")
BETA = declare_feature("beta", label="Beta", description="Try what's coming.")
# What a service checks beta with - so beta, unlocking no setting, is still read.
needs_beta = requires_feature(BETA)
INTERNAL = declare_feature("internal_flag", label="Internal", visible_to_holder=False)

declare_setting(
    "max_endpoints",
    models.PositiveIntegerField(validators=[MaxValueValidator(50)]),
    # 5 with webhooks, none without - staff's to raise, not the holder's.
    default=5,
    feature=WEBHOOKS,
    without_feature=Fixed(0),
    holder_may_change=False,
    label="Webhook endpoints",
    description="How many endpoints you may register.",
)
declare_setting(
    "token_lifetime_days",
    models.PositiveIntegerField(),
    default=30,
    feature=LONG_LIVED_TOKENS,
    without_feature=AtMost(90),
    label="Token lifetime",
)
declare_setting(
    "two_factor_required",
    models.BooleanField(),
    default=False,
    label="Two-factor sign-in",
    request_policies=(RecentlyProved,),
)
declare_setting(
    "theme",
    models.CharField(max_length=10, choices=[("light", "Light"), ("dark", "Dark")]),
    default="light",
    label="Theme",
)
declare_setting("session_length", models.DurationField(), default=timedelta(hours=8), label="Session length")
# The user's first_name is its own; user.display_name reads what's in force.
declare_setting(
    "display_name",
    models.CharField(max_length=150),
    default="",
    own=OnHolder("first_name", resolved_as="display_name"),
    label="Name",
)
declare_setting(
    "staff_note", models.TextField(), default="", label="Staff note", visible_to_holder=False, feature=INTERNAL
)
