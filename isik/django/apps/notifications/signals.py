from django.dispatch import Signal


# Sent with `endpoint` when DISABLE_AFTER failures in a row park it - inside its partition, in the
# transaction that parks it. A project tells the endpoint's owners from here, with a kind of its own.
endpoint_parked = Signal()

# Sent once the post-migrate mirror commits a kind's webhook policy moving to `never`, with `kind`, its name, and
# `endpoint_ids`, the endpoints subscribed to it, as text - never none. Their subscriptions stay, inert: fan-out
# sends nothing on them. isik doesn't know who owns an endpoint, so a project tells the owners from here, with a
# kind of its own - and the operators, who may mute theirs.
endpoint_kinds_withdrawn = Signal()
# Sent likewise once a kind's webhook policy moves back off `never`: the endpoints' subscriptions to it send again.
endpoint_kinds_restored = Signal()
