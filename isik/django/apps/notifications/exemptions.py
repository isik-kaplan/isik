"""
The reasons a project gives `ISIK_NOTIFICATIONS` for what a check would otherwise warn of - each typed, each
held to a reason worth reading, each listed by `manage.py exemptions`. Nothing here imports a model, so a
settings module imports them:

- `ScheduledElsewhere` - `FAN_OUT_BY`: what starts the fan-out where isik can't see it (`isik.W006`).
- `SendsInline` - `SENDS_INLINE`: why sending webhooks inside the writing request is fine (`isik.W007`).
- `SharedEndpoints` - `ENDPOINTS_SHARED`: why endpoints keyed to a user are still the deployment's
  (`isik.W008`).
"""

from isik.common.utils.exemptions import Exemption


class ScheduledElsewhere(
    Exemption,
    rule="isik.notifications.fan-out-started",
    why=(
        "A notice waits until something starts a fan-out, and isik sees only its middleware, FAN_OUT_AFTER_TASKS "
        "and beat_schedule() in the Celery app's beat."
    ),
):
    """What starts the fan-out where isik can't see it - `ISIK_NOTIFICATIONS["FAN_OUT_BY"]`."""


class SendsInline(
    Exemption,
    rule="isik.notifications.sends-inline",
    why="With tasks run inline, a webhook is sent inside the request or task that wrote its notice.",
):
    """Why sending inside the writing request or task is acceptable - `ISIK_NOTIFICATIONS["SENDS_INLINE"]`."""


class SharedEndpoints(
    Exemption,
    rule="isik.notifications.shared-endpoints",
    why=(
        "With no ENDPOINT_OWNER_FIELD every endpoint hears every notice of its kinds - an endpoint keyed to a "
        "user is sent every other user's."
    ),
):
    """Why endpoints keyed to a user are still the deployment's - `ISIK_NOTIFICATIONS["ENDPOINTS_SHARED"]`."""
