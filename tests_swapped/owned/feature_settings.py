"""The swapped project's features and settings - an organization's."""

from django.db import models

from isik.django.apps.common.forcing import declare_force_source
from isik.django.apps.features.declarations import AtMost, OnHolder, declare_feature, declare_setting


STAFF = declare_force_source("staff", label="Staff", shows_reason_to_holder=True)

WEBHOOKS = declare_feature("webhooks", label="Webhooks")
LARGE_TEAMS = declare_feature("large_teams", label="Large teams")

declare_setting("max_endpoints", models.PositiveIntegerField(), 0, feature=WEBHOOKS, label="Webhook endpoints")
# The organization's own field - its own value lives on the holder, where its API already has it.
declare_setting(
    "seat_limit",
    models.PositiveIntegerField(),
    5,
    feature=LARGE_TEAMS,
    without_feature=AtMost(10),
    own=OnHolder("seat_limit"),
    label="Seats",
)
