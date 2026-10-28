"""
`isik.django.apps.notifications.audience` - the notification rows that name people: subscriptions, inbox
items, endpoints and the kinds each endpoint wants. Install it beside `isik.django.apps.notifications`,
the ledger, which it can't work without. Each model is swappable (see `swapping.py`), and its abstract
base is in `bases.py`.
"""

from isik.django.apps.notifications.audience.swapping import (
    get_endpoint_kind_model,
    get_endpoint_model,
    get_inbox_item_model,
    get_subscription_model,
)


__all__ = [
    "get_endpoint_kind_model",
    "get_endpoint_model",
    "get_inbox_item_model",
    "get_subscription_model",
]
