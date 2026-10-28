from django.dispatch import Signal


# Sent with `endpoint` when DISABLE_AFTER failures in a row park it - inside its partition, in the
# transaction that parks it. A project tells the endpoint's owners from here, with a kind of its own.
endpoint_parked = Signal()
